from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest

from bot.reactions import (
    DiscordNotFound,
    _fetch_ballot_message,
    _set_vote_reaction,
    update_election_vote_reaction,
)
from tests.utils import DummyResult, DummySession, session_cm


@pytest.mark.asyncio
async def test_update_reaction_sets_correct_emoji(monkeypatch):
    reaction = SimpleNamespace(me=True)
    reaction.remove = AsyncMock()
    message = SimpleNamespace(reactions=[reaction])
    message.add_reaction = AsyncMock()

    session = DummySession(
        execute_results=[DummyResult(scalar=7)],
        get_results={
            1: SimpleNamespace(id=1, ballot_message_id=123, vote_reaction_frozen=False)
        },
    )
    monkeypatch.setattr("bot.reactions.async_session", lambda: session_cm(session))
    monkeypatch.setattr(
        "bot.reactions._fetch_ballot_message",
        AsyncMock(return_value=message),
    )

    client = SimpleNamespace(user=SimpleNamespace(id=99))

    await update_election_vote_reaction(client, 1)

    reaction.remove.assert_awaited_once_with(client.user)
    assert message.add_reaction.await_args_list == [call("7️⃣")]


@pytest.mark.asyncio
async def test_update_reaction_skips_without_message(monkeypatch):
    session = DummySession(
        execute_results=[],
        get_results={
            1: SimpleNamespace(id=1, ballot_message_id=None, vote_reaction_frozen=False)
        },
    )
    monkeypatch.setattr("bot.reactions.async_session", lambda: session_cm(session))
    fetch_mock = AsyncMock()
    monkeypatch.setattr("bot.reactions._fetch_ballot_message", fetch_mock)

    client = SimpleNamespace(user=SimpleNamespace(id=99))

    await update_election_vote_reaction(client, 1)

    fetch_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_reaction_freezes_at_eleven(monkeypatch):
    reaction = SimpleNamespace(me=True)
    reaction.remove = AsyncMock()
    message = SimpleNamespace(reactions=[reaction])
    message.add_reaction = AsyncMock()

    election_one = SimpleNamespace(
        id=1, ballot_message_id=321, vote_reaction_frozen=False
    )
    session_one = DummySession(
        execute_results=[DummyResult(scalar=12)],
        get_results={1: election_one},
    )

    election_two = SimpleNamespace(
        id=1, ballot_message_id=321, vote_reaction_frozen=False
    )
    session_two = DummySession(get_results={1: election_two})

    sessions = iter([session_one, session_two])

    monkeypatch.setattr(
        "bot.reactions.async_session", lambda: session_cm(next(sessions))
    )
    fetch_mock = AsyncMock(return_value=message)
    monkeypatch.setattr("bot.reactions._fetch_ballot_message", fetch_mock)

    client = SimpleNamespace(user=SimpleNamespace(id=99))

    await update_election_vote_reaction(client, 1)

    reaction.remove.assert_awaited_once_with(client.user)
    assert message.add_reaction.await_args_list == [call("🔟"), call("➕")]
    assert election_two.vote_reaction_frozen is True
    assert session_two.commit_calls == 1


@pytest.mark.asyncio
async def test_update_reaction_skips_when_frozen(monkeypatch):
    session = DummySession(
        execute_results=[DummyResult(scalar=15)],
        get_results={
            1: SimpleNamespace(id=1, ballot_message_id=555, vote_reaction_frozen=True)
        },
    )
    monkeypatch.setattr("bot.reactions.async_session", lambda: session_cm(session))
    fetch_mock = AsyncMock()
    monkeypatch.setattr("bot.reactions._fetch_ballot_message", fetch_mock)

    client = SimpleNamespace(user=SimpleNamespace(id=99))

    await update_election_vote_reaction(client, 1)

    fetch_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_reaction_bails_when_message_missing(monkeypatch):
    session = DummySession(
        execute_results=[DummyResult(scalar=3)],
        get_results={
            1: SimpleNamespace(id=1, ballot_message_id=789, vote_reaction_frozen=False)
        },
    )
    monkeypatch.setattr("bot.reactions.async_session", lambda: session_cm(session))
    fetch_mock = AsyncMock(return_value=None)
    monkeypatch.setattr("bot.reactions._fetch_ballot_message", fetch_mock)
    set_mock = AsyncMock()
    monkeypatch.setattr("bot.reactions._set_vote_reaction", set_mock)

    client = SimpleNamespace(user=SimpleNamespace(id=99))

    await update_election_vote_reaction(client, 1)

    fetch_mock.assert_awaited_once()
    set_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_fetch_ballot_message_returns_none_without_channel():
    client = SimpleNamespace(
        get_channel=lambda _: None,
        fetch_channel=AsyncMock(return_value=None),
    )

    result = await _fetch_ballot_message(client, 1)

    assert result is None


@pytest.mark.asyncio
async def test_fetch_ballot_message_requires_fetch_method():
    channel = SimpleNamespace()
    client = SimpleNamespace(get_channel=lambda _: channel)

    result = await _fetch_ballot_message(client, 1)

    assert result is None


@pytest.mark.asyncio
async def test_fetch_ballot_message_handles_not_found():
    fetch_mock = AsyncMock(side_effect=DiscordNotFound("missing"))
    channel = SimpleNamespace(fetch_message=fetch_mock)
    client = SimpleNamespace(get_channel=lambda _: channel)

    result = await _fetch_ballot_message(client, 1)

    assert result is None
    fetch_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_vote_reaction_skips_when_user_missing():
    client = SimpleNamespace(user=None)
    message = SimpleNamespace(reactions=[])

    freeze = await _set_vote_reaction(client, message, 5)

    assert freeze is False
