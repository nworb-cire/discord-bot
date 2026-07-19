from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from loguru import logger
from openai import AsyncOpenAI, OpenAIError
from pydantic import BaseModel, Field, ValidationError, field_validator

from bot.config import get_settings
from bot.db import Prediction

settings = get_settings()
MAX_LOG_PREDICTION_LENGTH = 500


class PredictionEvidenceLookupError(Exception):
    pass


class PredictionEvidence(BaseModel):
    url: str
    summary: str
    direction: Literal["supporting", "contradictory"]

    @field_validator("summary")
    @classmethod
    def _require_summary(cls, value: str) -> str:
        value = " ".join(str(value or "").split())
        if not value:
            raise ValueError("summary must not be empty")
        return value

    @field_validator("url")
    @classmethod
    def _require_url(cls, value: str) -> str:
        value = " ".join(str(value or "").split())
        if not value.startswith(("http://", "https://")):
            raise ValueError("url must be an absolute HTTP URL")
        return value


class PredictionEvidenceResult(BaseModel):
    evidence: list[PredictionEvidence] = Field(default_factory=list)


def _log_text(value: str, max_length: int = MAX_LOG_PREDICTION_LENGTH) -> str:
    value = " ".join(str(value).split())
    if len(value) <= max_length:
        return value
    return f"{value[: max_length - 3]}..."


def _openai_error_details(exc: BaseException) -> dict[str, Any]:
    return {
        "type": type(exc).__name__,
        "status_code": getattr(exc, "status_code", None),
        "request_id": getattr(exc, "request_id", None),
        "code": getattr(exc, "code", None),
    }


def _openai_response_details(response: Any) -> dict[str, Any]:
    return {
        "id": getattr(response, "id", None),
        "model": getattr(response, "model", None),
        "status": getattr(response, "status", None),
        "incomplete_details": getattr(response, "incomplete_details", None),
        "usage": getattr(response, "usage", None),
    }


def _prediction_datetime_text(value: datetime | None) -> str:
    if value is None:
        return "unknown"
    return value.isoformat(sep=" ")


async def find_prediction_evidence(
    prediction: Prediction,
) -> list[PredictionEvidence]:
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    log_prediction = _log_text(prediction.text)
    logger.info(
        "Looking up prediction evidence with OpenAI model={} prediction_id={} "
        "prediction={!r}",
        settings.openai_prediction_evidence_model,
        getattr(prediction, "id", None),
        log_prediction,
    )

    try:
        response = await client.responses.parse(
            model=settings.openai_prediction_evidence_model,
            reasoning={"effort": settings.openai_prediction_evidence_reasoning_effort},
            instructions=(
                "Use web search to find conclusive evidence for or against the "
                "prediction. Include a source URL and a brief explanation for each "
                "result. Return no evidence when the available sources are "
                "inconclusive."
            ),
            input=(
                "Find conclusive evidence for or against this prediction.\n"
                f"Prediction: {prediction.text}\n"
                "Prediction made at: "
                f"{_prediction_datetime_text(getattr(prediction, 'created_at', None))}\n"
                "When applicable, consider only events or changes occurring after "
                "the prediction was made."
            ),
            text_format=PredictionEvidenceResult,
            tools=[{"type": "web_search", "search_context_size": "low"}],
            max_output_tokens=settings.openai_prediction_evidence_max_output_tokens,
        )
    except OpenAIError as exc:
        logger.exception(
            "OpenAI prediction evidence request failed prediction_id={} "
            "prediction={!r} model={} details={}",
            getattr(prediction, "id", None),
            log_prediction,
            settings.openai_prediction_evidence_model,
            _openai_error_details(exc),
        )
        raise PredictionEvidenceLookupError from exc
    except (ValidationError, ValueError) as exc:
        logger.exception(
            "OpenAI prediction evidence returned invalid structured output "
            "prediction_id={} prediction={!r} model={} error_type={}",
            getattr(prediction, "id", None),
            log_prediction,
            settings.openai_prediction_evidence_model,
            type(exc).__name__,
        )
        raise PredictionEvidenceLookupError from exc

    result = response.output_parsed
    if not isinstance(result, PredictionEvidenceResult):
        response_status = getattr(response, "status", None)
        if response_status == "incomplete":
            logger.error(
                "OpenAI prediction evidence response was incomplete "
                "prediction_id={} prediction={!r} model={} response={}",
                getattr(prediction, "id", None),
                log_prediction,
                settings.openai_prediction_evidence_model,
                _openai_response_details(response),
            )
            raise PredictionEvidenceLookupError(
                "OpenAI prediction evidence response was incomplete"
            )
        logger.error(
            "OpenAI prediction evidence response did not include parsed output "
            "prediction_id={} prediction={!r} model={} parsed_type={} response={}",
            getattr(prediction, "id", None),
            log_prediction,
            settings.openai_prediction_evidence_model,
            type(result).__name__,
            _openai_response_details(response),
        )
        raise PredictionEvidenceLookupError("OpenAI did not return prediction evidence")

    logger.info(
        "OpenAI prediction evidence returned prediction_id={} evidence_count={} "
        "response={}",
        getattr(prediction, "id", None),
        len(result.evidence),
        _openai_response_details(response),
    )
    return result.evidence
