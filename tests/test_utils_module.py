from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from bot.utils import get_open_election, parse_due_date, parse_due_datetime, utcnow
from tests.utils import DummyResult, DummySession


def test_utcnow_returns_timezone_aware():
    now = utcnow()
    assert now.tzinfo is not None
    assert now.tzinfo.utcoffset(now).total_seconds() == 0


@pytest.mark.asyncio
async def test_get_open_election_returns_latest():
    election = SimpleNamespace(id=1)
    session = DummySession(execute_results=[DummyResult(scalar=election)])

    result = await get_open_election(session)

    assert result is election


@pytest.mark.asyncio
async def test_get_open_election_handles_absence():
    session = DummySession(execute_results=[DummyResult(scalar=None)])

    result = await get_open_election(session)

    assert result is None


def test_parse_due_datetime_accepts_date():
    expected = datetime(2024, 1, 2, tzinfo=ZoneInfo("America/Denver"))
    assert parse_due_datetime("2024-01-02") == expected


def test_parse_due_date_accepts_datetime_with_timezone():
    result = parse_due_datetime("2024-01-02T05:00:00+02:00")
    expected = datetime(2024, 1, 1, 20, 0, tzinfo=ZoneInfo("America/Denver"))
    assert result == expected


def test_parse_due_date_alias_returns_datetime():
    result = parse_due_date("2024-01-02")
    assert isinstance(result, datetime)
    assert result.tzinfo == ZoneInfo("America/Denver")


def test_parse_due_date_accepts_natural_language(monkeypatch):
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr("bot.utils.utcnow", lambda: base)
    result = parse_due_datetime("next week")
    assert result.tzinfo == ZoneInfo("America/Denver")
    expected = base.astimezone(ZoneInfo("America/Denver")) + timedelta(weeks=1)
    assert result == expected


def test_parse_due_date_rejects_bad_input():
    with pytest.raises(ValueError):
        parse_due_date("bad-date")
