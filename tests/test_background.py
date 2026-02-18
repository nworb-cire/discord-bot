# tests/test_background.py
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
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
        monkeypatch.setattr(
            background_mod, "get_open_election", AsyncMock(return_value=election)
        )
        tally = AsyncMock()
        monkeypatch.setattr(background_mod, "close_and_tally", tally)
        monkeypatch.setattr(
            background_mod, "async_session", lambda: _session_cm(session)
        )

        await background_mod.close_expired_elections(client)

        tally.assert_awaited_once_with(client, session, election)

    asyncio.run(_run())


def test_close_expired_elections_ignores_active(monkeypatch):
    async def _run():
        now = datetime(2024, 1, 1, tzinfo=timezone.utc)
        election = SimpleNamespace(closes_at=now + timedelta(hours=1))
        session = object()

        monkeypatch.setattr(background_mod, "utcnow", lambda: now)
        monkeypatch.setattr(
            background_mod, "get_open_election", AsyncMock(return_value=election)
        )
        tally = AsyncMock()
        monkeypatch.setattr(background_mod, "close_and_tally", tally)
        monkeypatch.setattr(
            background_mod, "async_session", lambda: _session_cm(session)
        )

        await background_mod.close_expired_elections(object())

        tally.assert_not_called()

    asyncio.run(_run())


def test_send_prediction_reminders_marks_and_notifies(monkeypatch):
    async def _run():
        now = datetime(2024, 1, 1, tzinfo=timezone.utc)
        local_due = now.astimezone(ZoneInfo("America/Denver")).replace(tzinfo=None)
        prediction = SimpleNamespace(
            text="Read more sci-fi",
            reminded=False,
            message_id=17,
            due_at=local_due,
        )
        session = SimpleNamespace()
        session.execute = AsyncMock(return_value=_FakeScalarResult([prediction]))
        session.commit = AsyncMock()
        channel = SimpleNamespace(
            send=AsyncMock(),
            id=5,
            guild=SimpleNamespace(id=9),
        )
        client = SimpleNamespace(get_channel=lambda _: channel)

        monkeypatch.setattr(background_mod, "utcnow", lambda: now)
        monkeypatch.setattr(
            background_mod, "settings", SimpleNamespace(predictions_channel_id=4)
        )
        monkeypatch.setattr(
            background_mod, "async_session", lambda: _session_cm(session)
        )

        await background_mod.send_prediction_reminders(client)

        assert prediction.reminded is True
        message = channel.send.await_args.args[0]
        assert "Reminder to adjudicate prediction" in message
        assert "> Read more sci-fi" in message
        assert "https://discord.com/channels/9/5/17" in message
        session.commit.assert_awaited_once()

    asyncio.run(_run())


def test_send_prediction_reminders_returns_without_predictions(monkeypatch):
    async def _run():
        now = datetime(2024, 1, 1, tzinfo=timezone.utc)
        session = SimpleNamespace()
        session.execute = AsyncMock(return_value=_FakeScalarResult([]))
        client = SimpleNamespace(get_channel=lambda _: SimpleNamespace())

        monkeypatch.setattr(background_mod, "utcnow", lambda: now)
        monkeypatch.setattr(
            background_mod, "async_session", lambda: _session_cm(session)
        )

        await background_mod.send_prediction_reminders(client)

        session.execute.assert_awaited_once()

    asyncio.run(_run())


def test_send_prediction_reminders_fetches_channel_when_missing(monkeypatch):
    async def _run():
        now = datetime(2024, 1, 1, tzinfo=timezone.utc)
        prediction = SimpleNamespace(
            text="Reminder", reminded=False, message_id=None, due_at=now
        )
        session = SimpleNamespace()
        session.execute = AsyncMock(return_value=_FakeScalarResult([prediction]))
        session.commit = AsyncMock()
        channel = SimpleNamespace(
            send=AsyncMock(),
            id=1,
            guild=SimpleNamespace(id=None),
        )
        client = SimpleNamespace(
            get_channel=lambda _: None, fetch_channel=AsyncMock(return_value=channel)
        )

        monkeypatch.setattr(background_mod, "utcnow", lambda: now)
        monkeypatch.setattr(
            background_mod, "settings", SimpleNamespace(predictions_channel_id=9)
        )
        monkeypatch.setattr(
            background_mod, "async_session", lambda: _session_cm(session)
        )

        await background_mod.send_prediction_reminders(client)

        client.fetch_channel.assert_awaited_once_with(9)

    asyncio.run(_run())


def test_run_calendar_sync_invokes_sync_runner(monkeypatch):
    async def _run():
        calls = []

        class _Runner:
            def __init__(self, settings):
                self.settings = settings

            def run(self):
                calls.append("run")

        async def _to_thread(func):
            func()

        monkeypatch.setattr(background_mod, "DiscordGoogleCalendarSync", _Runner)
        monkeypatch.setattr(background_mod.asyncio, "to_thread", _to_thread)

        await background_mod.run_calendar_sync()

        assert calls == ["run"]

    asyncio.run(_run())


def test_run_calendar_sync_logs_sync_errors(monkeypatch):
    async def _run():
        class _Runner:
            def __init__(self, settings):
                self.settings = settings

            def run(self):
                raise background_mod.SyncError("boom")

        async def _to_thread(func):
            func()

        called = {"exception": 0}

        def _log_exception(*_args, **_kwargs):
            called["exception"] += 1

        monkeypatch.setattr(background_mod, "DiscordGoogleCalendarSync", _Runner)
        monkeypatch.setattr(background_mod.asyncio, "to_thread", _to_thread)
        monkeypatch.setattr(
            background_mod, "logger", SimpleNamespace(exception=_log_exception)
        )

        await background_mod.run_calendar_sync()

        assert called["exception"] == 1

    asyncio.run(_run())


def test_run_recurring_event_creation_invokes_runner(monkeypatch):
    async def _run():
        calls = []

        class _Runner:
            def __init__(self, settings):
                self.settings = settings

            def run(self):
                calls.append("run")

        async def _to_thread(func):
            func()

        monkeypatch.setattr(background_mod, "RecurringDiscordEventCreator", _Runner)
        monkeypatch.setattr(background_mod.asyncio, "to_thread", _to_thread)

        await background_mod.run_recurring_event_creation()

        assert calls == ["run"]

    asyncio.run(_run())


def test_run_recurring_event_creation_logs_errors(monkeypatch):
    async def _run():
        class _Runner:
            def __init__(self, settings):
                self.settings = settings

            def run(self):
                raise background_mod.RecurringEventError("boom")

        async def _to_thread(func):
            func()

        called = {"exception": 0}

        def _log_exception(*_args, **_kwargs):
            called["exception"] += 1

        monkeypatch.setattr(background_mod, "RecurringDiscordEventCreator", _Runner)
        monkeypatch.setattr(background_mod.asyncio, "to_thread", _to_thread)
        monkeypatch.setattr(
            background_mod, "logger", SimpleNamespace(exception=_log_exception)
        )

        await background_mod.run_recurring_event_creation()

        assert called["exception"] == 1

    asyncio.run(_run())
