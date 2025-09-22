# tests/test_background.py
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot import background as background_mod


def _session_cm(session):
    @asynccontextmanager
    async def _cm():
        yield session

    return _cm()


class _FakeScalarResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return list(self._items)


def test_close_expired_elections_triggers_tally(monkeypatch):
    async def _run():
        now = datetime(2024, 1, 1, tzinfo=timezone.utc)
        election = SimpleNamespace(closes_at=now - timedelta(hours=1))
        session = object()
        client = object()

        monkeypatch.setattr(background_mod, "utcnow", lambda: now)
        monkeypatch.setattr(background_mod, "get_open_election", AsyncMock(return_value=election))
        tally = AsyncMock()
        monkeypatch.setattr(background_mod, "close_and_tally", tally)
        monkeypatch.setattr(background_mod, "async_session", lambda: _session_cm(session))

        await background_mod.close_expired_elections(client)

        tally.assert_awaited_once_with(client, session, election)

    asyncio.run(_run())


def test_close_expired_elections_ignores_active(monkeypatch):
    async def _run():
        now = datetime(2024, 1, 1, tzinfo=timezone.utc)
        election = SimpleNamespace(closes_at=now + timedelta(hours=1))
        session = object()

        monkeypatch.setattr(background_mod, "utcnow", lambda: now)
        monkeypatch.setattr(background_mod, "get_open_election", AsyncMock(return_value=election))
        tally = AsyncMock()
        monkeypatch.setattr(background_mod, "close_and_tally", tally)
        monkeypatch.setattr(background_mod, "async_session", lambda: _session_cm(session))

        await background_mod.close_expired_elections(object())

        tally.assert_not_called()

    asyncio.run(_run())


def test_send_prediction_reminders_marks_and_notifies(monkeypatch):
    async def _run():
        now = datetime(2024, 1, 1, tzinfo=timezone.utc)
        prediction = SimpleNamespace(text="Read more sci-fi", reminded=False)
        session = SimpleNamespace()
        session.execute = AsyncMock(return_value=_FakeScalarResult([prediction]))
        session.commit = AsyncMock()
        channel = SimpleNamespace(send=AsyncMock())
        client = SimpleNamespace(get_channel=lambda _: channel)

        monkeypatch.setattr(background_mod, "utcnow", lambda: now)
        monkeypatch.setattr(background_mod, "settings", SimpleNamespace(predictions_channel_id=4))
        monkeypatch.setattr(background_mod, "async_session", lambda: _session_cm(session))

        await background_mod.send_prediction_reminders(client)

        assert prediction.reminded is True
        message = channel.send.await_args.args[0]
        assert "Reminder to adjudicate prediction" in message
        assert "> Read more sci-fi" in message
        session.commit.assert_awaited_once()

    asyncio.run(_run())
