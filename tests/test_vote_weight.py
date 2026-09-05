# tests/test_vote_weight.py
from bot.config import Settings


def test_vote_weights(monkeypatch):
    monkeypatch.setenv("VOTE_WEIGHT_INNER", "200")
    monkeypatch.setenv("VOTE_WEIGHT_OUTER", "50")
    s = Settings()
    assert s.weight_inner == 200
    assert s.weight_outer == 50


def test_openai_book_lookup_settings(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")  # pragma: allowlist secret
    monkeypatch.delenv("OPENAI_BOOK_LOOKUP_MODEL", raising=False)
    s = Settings()
    assert s.openai_api_key == "test-key"  # pragma: allowlist secret
    assert s.openai_book_lookup_model == "gpt-5-mini"
    assert s.openai_book_lookup_reasoning_effort == "low"
    assert s.openai_book_lookup_max_output_tokens == 4000
    assert s.openai_prediction_evidence_model == "gpt-5.6-luna"
    assert s.openai_prediction_evidence_reasoning_effort == "high"
    assert s.openai_prediction_evidence_max_output_tokens == 2000
    assert s.openai_summarization_model == "gpt-5.6-luna"
    assert s.openai_summarization_reasoning_effort == "low"
    assert s.openai_summarization_max_output_tokens == 3000
    assert s.summarization_history_limit == 500
    assert s.summarization_max_input_chars == 600_000
    assert s.summarization_hard_gap_hours == 6.0
    assert s.summarization_soft_gap_minutes == 90.0

    monkeypatch.setenv("OPENAI_BOOK_LOOKUP_MODEL", "custom-model")
    monkeypatch.setenv("OPENAI_BOOK_LOOKUP_REASONING_EFFORT", "medium")
    monkeypatch.setenv("OPENAI_BOOK_LOOKUP_MAX_OUTPUT_TOKENS", "6000")
    monkeypatch.setenv("OPENAI_PREDICTION_EVIDENCE_MODEL", "evidence-model")
    monkeypatch.setenv("OPENAI_PREDICTION_EVIDENCE_REASONING_EFFORT", "medium")
    monkeypatch.setenv("OPENAI_PREDICTION_EVIDENCE_MAX_OUTPUT_TOKENS", "1000")
    s = Settings()
    assert s.openai_book_lookup_model == "custom-model"
    assert s.openai_book_lookup_reasoning_effort == "medium"
    assert s.openai_book_lookup_max_output_tokens == 6000
    assert s.openai_prediction_evidence_model == "evidence-model"
    assert s.openai_prediction_evidence_reasoning_effort == "medium"
    assert s.openai_prediction_evidence_max_output_tokens == 1000
