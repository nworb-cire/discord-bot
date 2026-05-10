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
    assert s.openai_book_lookup_reasoning_effort == "minimal"
    assert s.openai_book_lookup_max_output_tokens == 4000

    monkeypatch.setenv("OPENAI_BOOK_LOOKUP_MODEL", "custom-model")
    monkeypatch.setenv("OPENAI_BOOK_LOOKUP_REASONING_EFFORT", "medium")
    monkeypatch.setenv("OPENAI_BOOK_LOOKUP_MAX_OUTPUT_TOKENS", "6000")
    s = Settings()
    assert s.openai_book_lookup_model == "custom-model"
    assert s.openai_book_lookup_reasoning_effort == "medium"
    assert s.openai_book_lookup_max_output_tokens == 6000
