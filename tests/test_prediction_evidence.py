from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.db import Prediction
from bot.prediction_evidence import (
    PredictionEvidence,
    PredictionEvidenceLookupError,
    PredictionEvidenceResult,
    find_prediction_evidence,
    settings,
)


@pytest.mark.asyncio
async def test_find_prediction_evidence_uses_openai_structured_output(monkeypatch):
    parsed = PredictionEvidenceResult(
        evidence=[
            PredictionEvidence(
                url="https://example.com/result",
                summary="The final official count shows the claim happened.",
                direction="supporting",
            )
        ]
    )
    parse_mock = AsyncMock(return_value=SimpleNamespace(output_parsed=parsed))

    class DummyOpenAI:
        def __init__(self, *, api_key):
            self.api_key = api_key
            self.responses = SimpleNamespace(parse=parse_mock)

    monkeypatch.setattr("bot.prediction_evidence.AsyncOpenAI", DummyOpenAI)
    prediction = Prediction(
        id=12,
        predictor_discord_id=42,
        text="A specific thing happens",
        odds=65,
        created_at=datetime(2024, 1, 1, 9, 30),
        due_at=datetime(2024, 1, 10, 12, 0),
        message_id=99,
    )

    evidence = await find_prediction_evidence(prediction)

    assert evidence == parsed.evidence
    parse_mock.assert_awaited_once()
    kwargs = parse_mock.await_args.kwargs
    assert kwargs["model"] == settings.openai_prediction_evidence_model
    assert kwargs["text_format"] is PredictionEvidenceResult
    assert kwargs["tools"] == [{"type": "web_search", "search_context_size": "low"}]
    assert kwargs["reasoning"] == {
        "effort": settings.openai_prediction_evidence_reasoning_effort
    }
    assert (
        kwargs["max_output_tokens"]
        == settings.openai_prediction_evidence_max_output_tokens
    )
    assert "A specific thing happens" in kwargs["input"]
    assert "Find conclusive evidence for or against" in kwargs["input"]
    assert "Prediction made at: 2024-01-01 09:30:00" in kwargs["input"]
    assert "2024-01-10" not in kwargs["input"]
    assert "source URL" in kwargs["instructions"]
    assert "conclusive" in kwargs["instructions"]
    assert "Discord server users" in kwargs["instructions"]
    assert "without searching the web" in kwargs["instructions"]
    assert "public news" in kwargs["instructions"]


@pytest.mark.asyncio
async def test_find_prediction_evidence_accepts_empty_evidence(monkeypatch):
    parsed = PredictionEvidenceResult(evidence=[])
    parse_mock = AsyncMock(return_value=SimpleNamespace(output_parsed=parsed))

    class DummyOpenAI:
        def __init__(self, *, api_key):
            self.responses = SimpleNamespace(parse=parse_mock)

    monkeypatch.setattr("bot.prediction_evidence.AsyncOpenAI", DummyOpenAI)
    prediction = SimpleNamespace(id=1, text="Ambiguous claim", due_at=None)

    evidence = await find_prediction_evidence(prediction)

    assert evidence == []


@pytest.mark.asyncio
async def test_find_prediction_evidence_rejects_invalid_structured_output(
    monkeypatch,
):
    parse_mock = AsyncMock(return_value=SimpleNamespace(output_parsed=None))

    class DummyOpenAI:
        def __init__(self, *, api_key):
            self.responses = SimpleNamespace(parse=parse_mock)

    monkeypatch.setattr("bot.prediction_evidence.AsyncOpenAI", DummyOpenAI)
    prediction = SimpleNamespace(id=1, text="A claim", due_at=None)

    with pytest.raises(PredictionEvidenceLookupError):
        await find_prediction_evidence(prediction)


def test_prediction_evidence_requires_absolute_http_url():
    with pytest.raises(ValueError):
        PredictionEvidence(
            url="example.com/result",
            summary="A summary",
            direction="supporting",
        )
