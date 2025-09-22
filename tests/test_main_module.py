import importlib
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

    main.bot.added_cogs.clear()

    await main.setup_commands()

    assert len(main.bot.added_cogs) == 3


@pytest.mark.asyncio
async def test_on_ready_syncs_commands(monkeypatch):
    monkeypatch.setattr(main, "setup_commands", AsyncMock())
    sync_mock = AsyncMock(return_value=[1])
    monkeypatch.setattr(main.bot, "tree", SimpleNamespace(sync=sync_mock))
    monkeypatch.setattr(main, "logger", SimpleNamespace(info=lambda *args, **kwargs: None))

    await main.on_ready()

    sync_mock.assert_awaited_once()
    assert getattr(main.election_auto_close, "started", False) is True
    assert getattr(main.prediction_reminder, "started", False) is True


@pytest.mark.asyncio
async def test_background_loops_call_tasks(monkeypatch):
    close_mock = AsyncMock()
    remind_mock = AsyncMock()
    monkeypatch.setattr(main, "close_expired_elections", close_mock)
    monkeypatch.setattr(main, "send_prediction_reminders", remind_mock)

    await main.election_auto_close()
    await main.prediction_reminder()

    close_mock.assert_awaited_once()
    remind_mock.assert_awaited_once()
