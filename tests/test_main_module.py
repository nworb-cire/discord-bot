from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import bot.main as main


@pytest.mark.asyncio
async def test_setup_commands_adds_cogs(monkeypatch):
    class FakeCog:
        def __init__(self, *args, **kwargs):
            self.args = args

    monkeypatch.setattr(main, "Nominate", FakeCog)
    monkeypatch.setattr(main, "VotingSession", FakeCog)
    monkeypatch.setattr(main, "Ballot", FakeCog)
    monkeypatch.setattr(main, "Predict", FakeCog)

    main.bot.added_cogs.clear()

    await main.setup_commands()

    assert len(main.bot.added_cogs) == 4


@pytest.mark.asyncio
async def test_on_ready_syncs_commands(monkeypatch):
    monkeypatch.setattr(main, "setup_commands", AsyncMock())
    sync_mock = AsyncMock(return_value=[1])
    monkeypatch.setattr(main.bot, "tree", SimpleNamespace(sync=sync_mock))
    monkeypatch.setattr(
        main, "logger", SimpleNamespace(info=lambda *args, **kwargs: None)
    )

    await main.on_ready()

    sync_mock.assert_awaited_once()
    assert getattr(main.election_auto_close, "started", False) is True
    assert getattr(main.prediction_reminder, "started", False) is True
    assert getattr(main.calendar_sync, "started", False) is True
    assert getattr(main.recurring_event_creation, "started", False) is True


@pytest.mark.asyncio
async def test_background_loops_call_tasks(monkeypatch):
    close_mock = AsyncMock()
    remind_mock = AsyncMock()
    sync_mock = AsyncMock()
    recurring_mock = AsyncMock()
    monkeypatch.setattr(main, "close_expired_elections", close_mock)
    monkeypatch.setattr(main, "send_prediction_reminders", remind_mock)
    monkeypatch.setattr(main, "run_calendar_sync", sync_mock)
    monkeypatch.setattr(main, "run_recurring_event_creation", recurring_mock)

    await main.election_auto_close()
    await main.prediction_reminder()
    await main.calendar_sync()

    close_mock.assert_awaited_once()
    remind_mock.assert_awaited_once()
    sync_mock.assert_awaited_once()

    monkeypatch.setattr(
        main, "utcnow", lambda: datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc)
    )
    await main.recurring_event_creation()
    recurring_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_recurring_event_creation_skips_non_first_day(monkeypatch):
    recurring_mock = AsyncMock()
    monkeypatch.setattr(main, "run_recurring_event_creation", recurring_mock)
    monkeypatch.setattr(
        main, "utcnow", lambda: datetime(2026, 2, 2, 12, 0, tzinfo=timezone.utc)
    )

    await main.recurring_event_creation()

    recurring_mock.assert_not_awaited()
