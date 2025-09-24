from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.commands.predict import Predict
from bot.db import Prediction
from tests.utils import DummyChannel, DummyInteraction, DummySession, session_cm


@pytest.mark.asyncio
async def test_predict_records_prediction(monkeypatch):
    session = DummySession()
    monkeypatch.setattr(
        "bot.commands.predict.async_session", lambda: session_cm(session)
    )
    channel = DummyChannel(5, guild_id=42)
    bot = SimpleNamespace(get_channel=lambda _cid: channel, fetch_channel=AsyncMock())
    interaction = DummyInteraction()

    cog = Predict(bot)

    await cog.predict(
        interaction, due="2024-01-10", text="We read more sci-fi", probability=0.6
    )

    assert len(channel.messages) == 1
    record = session.added[0]
    assert isinstance(record, Prediction)
    assert record.text == "We read more sci-fi"
    assert float(record.odds) == pytest.approx(60.0)
    assert str(record.due_date) == "2024-01-10"
    assert session.commit_calls == 1
    response = interaction.response.messages[0]
    assert "Prediction scheduled" in response["content"]
    assert "View it" in response["content"]


@pytest.mark.asyncio
async def test_predict_accepts_percentage(monkeypatch):
    session = DummySession()
    monkeypatch.setattr(
        "bot.commands.predict.async_session", lambda: session_cm(session)
    )
    channel = DummyChannel(5, guild_id=42)
    bot = SimpleNamespace(get_channel=lambda _cid: channel, fetch_channel=AsyncMock())
    interaction = DummyInteraction()

    cog = Predict(bot)

    await cog.predict(interaction, due="2024-01-10", text="It rains", probability=75)

    record = session.added[0]
    assert float(record.odds) == pytest.approx(75.0)


@pytest.mark.asyncio
async def test_predict_handles_invalid_date(monkeypatch):
    session = DummySession()
    monkeypatch.setattr(
        "bot.commands.predict.async_session", lambda: session_cm(session)
    )
    channel = DummyChannel(5, guild_id=42)
    bot = SimpleNamespace(get_channel=lambda _cid: channel, fetch_channel=AsyncMock())
    interaction = DummyInteraction()

    cog = Predict(bot)

    await cog.predict(interaction, due="not-a-date", text="Test", probability=None)

    error_message = interaction.response.messages[0]["content"]
    assert "Could not parse due date" in error_message


@pytest.mark.asyncio
async def test_predict_parses_natural_language(monkeypatch):
    session = DummySession()
    monkeypatch.setattr(
        "bot.commands.predict.async_session", lambda: session_cm(session)
    )
    channel = DummyChannel(5, guild_id=42)
    bot = SimpleNamespace(get_channel=lambda _cid: channel, fetch_channel=AsyncMock())
    interaction = DummyInteraction()

    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr("bot.utils.utcnow", lambda: base)

    cog = Predict(bot)

    await cog.predict(
        interaction, due="next week", text="We finish the book", probability=None
    )

    record = session.added[0]
    assert str(record.due_date) == "2024-01-08"
