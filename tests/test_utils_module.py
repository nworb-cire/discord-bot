import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from bot import utils as utils_mod
from bot.utils import (
    format_vote_count,
    get_open_election,
    handle_interaction_errors,
    nomination_message_url,
    parse_due_date,
    parse_due_datetime,
    utcnow,
)
from tests.utils import DummyInteraction, DummyResult, DummySession


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


def test_format_vote_count_handles_nonfinite():
    assert format_vote_count(float("inf")) == "inf"


def test_parse_due_datetime_rejects_empty_string():
    with pytest.raises(ValueError):
        parse_due_datetime("   ")


def test_parse_due_datetime_assigns_timezone_when_missing(monkeypatch):
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr("bot.utils.utcnow", lambda: base)
    naive = base.replace(tzinfo=None)
    monkeypatch.setattr("bot.utils.dateparser.parse", lambda *_, **__: naive)

    result = parse_due_datetime("tomorrow")

    assert result.tzinfo == ZoneInfo("America/Denver")


def test_nomination_message_url_requires_guild():
    assert nomination_message_url(5, None) is None


@pytest.mark.asyncio
async def test_handle_interaction_errors_passes_through_without_interaction():
    calls = []

    @handle_interaction_errors()
    async def handler(value):
        calls.append(value)

    await handler("ok")

    assert calls == ["ok"]


@pytest.mark.asyncio
async def test_handle_interaction_errors_handles_timeout():
    interaction = DummyInteraction()

    @handle_interaction_errors()
    async def handler(interaction):
        raise asyncio.TimeoutError("boom")

    await handler(interaction)

    assert (
        interaction.response.messages[0]["content"]
        == "Request timed out. Please try again."
    )


@pytest.mark.asyncio
async def test_handle_interaction_errors_handles_generic():
    interaction = DummyInteraction()

    @handle_interaction_errors("Generic failure")
    async def handler(interaction):
        raise RuntimeError("boom")

    await handler(interaction)

    assert interaction.response.messages[0]["content"] == "Generic failure"


def test_extract_interaction_prefers_kwargs():
    interaction = DummyInteraction()
    result = utils_mod._extract_interaction((), {"interaction": interaction})
    assert result is interaction


def test_extract_interaction_checks_args():
    interaction = DummyInteraction()
    result = utils_mod._extract_interaction((interaction,), {})
    assert result is interaction


def test_interaction_already_handled_uses_is_done():
    response = SimpleNamespace(is_done=lambda: True)
    assert utils_mod._interaction_already_handled(response) is True


def test_interaction_already_handled_checks_messages():
    response = SimpleNamespace(messages=[SimpleNamespace()])
    assert utils_mod._interaction_already_handled(response) is True
