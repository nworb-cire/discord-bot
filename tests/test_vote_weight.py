# tests/test_vote_weight.py
from bot.config import Settings


def test_vote_weights(monkeypatch):
    monkeypatch.setenv("VOTE_WEIGHT_INNER", "200")
    monkeypatch.setenv("VOTE_WEIGHT_OUTER", "50")
    s = Settings()
    assert s.weight_inner == 200
    assert s.weight_outer == 50
