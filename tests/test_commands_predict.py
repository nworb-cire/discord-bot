from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from zoneinfo import ZoneInfo

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
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr("bot.utils.utcnow", lambda: base)
    monkeypatch.setattr("bot.commands.predict.utcnow", lambda: base)

    cog = Predict(bot)

    await cog.predict(
        interaction, due="2024-01-10", text="We read more sci-fi", probability=60
    )

    assert len(channel.messages) == 1
    record = session.added[0]
    assert isinstance(record, Prediction)
    assert record.text == "We read more sci-fi"
    assert float(record.odds) == pytest.approx(60.0)
    assert record.due_at == datetime(2024, 1, 10, 0, 0)
    assert session.commit_calls == 1
    response = interaction.response.messages[0]
    assert response["ephemeral"] is True


@pytest.mark.asyncio
async def test_predict_accepts_percentage(monkeypatch):
    session = DummySession()
    monkeypatch.setattr(
        "bot.commands.predict.async_session", lambda: session_cm(session)
    )
    channel = DummyChannel(5, guild_id=42)
    bot = SimpleNamespace(get_channel=lambda _cid: channel, fetch_channel=AsyncMock())
    interaction = DummyInteraction()
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr("bot.utils.utcnow", lambda: base)
    monkeypatch.setattr("bot.commands.predict.utcnow", lambda: base)

    cog = Predict(bot)

    await cog.predict(interaction, due="2024-01-10", text="It rains", probability=75)

    record = session.added[0]
    assert float(record.odds) == pytest.approx(75.0)


@pytest.mark.asyncio
async def test_predict_accepts_datetime_input(monkeypatch):
    session = DummySession()
    monkeypatch.setattr(
        "bot.commands.predict.async_session", lambda: session_cm(session)
    )
    channel = DummyChannel(5, guild_id=42)
    bot = SimpleNamespace(get_channel=lambda _cid: channel, fetch_channel=AsyncMock())
    interaction = DummyInteraction()
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr("bot.utils.utcnow", lambda: base)
    monkeypatch.setattr("bot.commands.predict.utcnow", lambda: base)

    cog = Predict(bot)

    await cog.predict(
        interaction,
        due="2024-01-10T15:30:00-05:00",
        text="A bold claim",
        probability=50,
    )

    record = session.added[0]
    assert record.due_at == datetime(2024, 1, 10, 13, 30)
    assert float(record.odds) == pytest.approx(50.0)


@pytest.mark.asyncio
async def test_predict_accepts_relative_minutes(monkeypatch):
    session = DummySession()
    monkeypatch.setattr(
        "bot.commands.predict.async_session", lambda: session_cm(session)
    )
    channel = DummyChannel(5, guild_id=42)
    bot = SimpleNamespace(get_channel=lambda _cid: channel, fetch_channel=AsyncMock())
    interaction = DummyInteraction()

    base = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("bot.utils.utcnow", lambda: base)
    monkeypatch.setattr("bot.commands.predict.utcnow", lambda: base)

    cog = Predict(bot)

    await cog.predict(
        interaction,
        due="in 2 minutes",
        text="Quick event",
        probability=40,
    )

    expected_local = base.astimezone(ZoneInfo("America/Denver")) + timedelta(minutes=2)
    record = session.added[0]
    assert record.due_at == expected_local.replace(tzinfo=None)
    assert float(record.odds) == pytest.approx(40.0)


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

    await cog.predict(interaction, due="not-a-date", text="Test", probability=50)

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
    monkeypatch.setattr("bot.commands.predict.utcnow", lambda: base)

    cog = Predict(bot)

    await cog.predict(
        interaction, due="next week", text="We finish the book", probability=80
    )

    record = session.added[0]
    expected_local = datetime(2024, 1, 1, tzinfo=timezone.utc).astimezone(
        ZoneInfo("America/Denver")
    ) + timedelta(weeks=1)
    assert record.due_at == expected_local.replace(tzinfo=None)
    assert float(record.odds) == pytest.approx(80.0)


@pytest.mark.asyncio
async def test_predict_rejects_probability_bounds(monkeypatch):
    session = DummySession()
    monkeypatch.setattr(
        "bot.commands.predict.async_session", lambda: session_cm(session)
    )
    channel = DummyChannel(5, guild_id=42)
    bot = SimpleNamespace(get_channel=lambda _cid: channel, fetch_channel=AsyncMock())
    interaction = DummyInteraction()
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr("bot.utils.utcnow", lambda: base)
    monkeypatch.setattr("bot.commands.predict.utcnow", lambda: base)

    cog = Predict(bot)

    await cog.predict(interaction, due="2024-01-10", text="Test", probability=0)

    error_message = interaction.response.messages[0]["content"]
    assert "between 0 and 100" in error_message


@pytest.mark.asyncio
async def test_predict_rejects_past_due(monkeypatch):
    session = DummySession()
    monkeypatch.setattr(
        "bot.commands.predict.async_session", lambda: session_cm(session)
    )
    channel = DummyChannel(5, guild_id=42)
    bot = SimpleNamespace(get_channel=lambda _cid: channel, fetch_channel=AsyncMock())
    interaction = DummyInteraction()

    base = datetime(2024, 1, 2, tzinfo=timezone.utc)
    monkeypatch.setattr("bot.utils.utcnow", lambda: base)
    monkeypatch.setattr("bot.commands.predict.utcnow", lambda: base)

    cog = Predict(bot)

    await cog.predict(
        interaction,
        due="2024-01-01",
        text="Past",
        probability=50,
    )

    error_message = interaction.response.messages[0]["content"]
    assert "future" in error_message
