from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.commands.voting_session import VotingSession
from bot.config import get_settings
from bot.db import Election
from bot.utils import NOMINATION_CANCEL_EMOJI
from tests.utils import (
    DummyChannel,
    DummyInteraction,
    DummyResult,
    DummySession,
    session_cm,
)

settings = get_settings()


@pytest.mark.asyncio
async def test_get_top_noms_returns_scores(monkeypatch):
    session = DummySession(
        execute_results=[
            DummyResult(
                rows=[SimpleNamespace(book_id=1, reactions=2, vote_sum=1.5, score=3.5)]
            )
        ]
    )
    vs = VotingSession(bot=SimpleNamespace())
    monkeypatch.setattr(vs, "update_all_nominations", AsyncMock())

    result = await vs.get_top_noms(session, limit=1)

    assert result == [(1, 2, 1.5, 3.5)]


@pytest.mark.asyncio
async def test_open_voting_aborts_if_open(monkeypatch):
    interaction = DummyInteraction()
    session = DummySession()
    vs = VotingSession(bot=SimpleNamespace())
    monkeypatch.setattr(
        "bot.commands.voting_session.async_session", lambda: session_cm(session)
    )
    monkeypatch.setattr(
        "bot.commands.voting_session.get_open_election",
        AsyncMock(return_value=SimpleNamespace()),
    )

    await vs.open_voting(interaction)

    assert interaction.response.deferred is True
    assert interaction.followup.messages[0]["content"] == "An election is already open."
    assert session.commit_calls == 0


@pytest.mark.asyncio
async def test_open_voting_creates_election(monkeypatch):
    interaction = DummyInteraction()
    session = DummySession()
    vs = VotingSession(bot=SimpleNamespace())
    monkeypatch.setattr(
        "bot.commands.voting_session.async_session", lambda: session_cm(session)
    )
    monkeypatch.setattr(
        "bot.commands.voting_session.get_open_election", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        vs, "get_top_noms", AsyncMock(return_value=[(1, 0, 0.0, 0.0), (2, 1, 2.0, 3.0)])
    )
    fake_embed = AsyncMock()
    monkeypatch.setattr(vs, "_election_embed", fake_embed)
    fixed_now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr("bot.commands.voting_session.utcnow", lambda: fixed_now)

    await vs.open_voting(interaction, hours=4)

    assert interaction.response.deferred is True
    vs.get_top_noms.assert_awaited_once_with(session, limit=5)
    election = next(obj for obj in session.added if isinstance(obj, Election))
    assert election.ballot == [1, 2]
    assert election.closes_at == fixed_now + timedelta(hours=4)
    fake_embed.assert_awaited_once()
    assert session.commit_calls == 1


@pytest.mark.asyncio
async def test_open_voting_accepts_custom_ballot_size(monkeypatch):
    interaction = DummyInteraction()
    session = DummySession()
    vs = VotingSession(bot=SimpleNamespace())
    monkeypatch.setattr(
        "bot.commands.voting_session.async_session", lambda: session_cm(session)
    )
    monkeypatch.setattr(
        "bot.commands.voting_session.get_open_election", AsyncMock(return_value=None)
    )
    ballot_mock = AsyncMock(return_value=[(1, 0, 0.0, 0.0)])
    monkeypatch.setattr(vs, "get_top_noms", ballot_mock)
    monkeypatch.setattr(vs, "_election_embed", AsyncMock())
    monkeypatch.setattr(
        "bot.commands.voting_session.utcnow",
        lambda: datetime(2024, 1, 1, tzinfo=timezone.utc),
    )

    await vs.open_voting(interaction, ballot_size=2)

    ballot_mock.assert_awaited_once_with(session, limit=2)


@pytest.mark.asyncio
async def test_open_voting_requires_ballot(monkeypatch):
    interaction = DummyInteraction()
    session = DummySession()
    vs = VotingSession(bot=SimpleNamespace())
    monkeypatch.setattr(
        "bot.commands.voting_session.async_session", lambda: session_cm(session)
    )
    monkeypatch.setattr(
        "bot.commands.voting_session.get_open_election", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(vs, "get_top_noms", AsyncMock(return_value=[]))

    await vs.open_voting(interaction)

    assert interaction.response.deferred is True
    assert (
        interaction.followup.messages[0]["content"]
        == "No nominations available for voting."
    )
    assert session.commit_calls == 0


@pytest.mark.asyncio
async def test_election_embed_posts_summary(monkeypatch):
    long_summary = "A" * 2000
    session = DummySession(
        execute_results=[
            DummyResult(scalars=[SimpleNamespace(book_id=1, message_id=99)]),
            DummyResult(
                scalars=[SimpleNamespace(id=1, title="Book", summary=long_summary)]
            ),
        ]
    )
    monkeypatch.setattr(
        "bot.commands.voting_session.async_session", lambda: session_cm(session)
    )
    vs = VotingSession(bot=SimpleNamespace())
    channel = DummyChannel(2)
    interaction = DummyInteraction(
        client=SimpleNamespace(get_channel=lambda _cid: channel)
    )
    interaction.guild_id = 123
    closes_at = datetime(2024, 1, 1, tzinfo=timezone.utc)

    await vs._election_embed(interaction, [1], closes_at)

    embed_entry = channel.messages[0]["embed"]
    assert embed_entry.title == "Book Club Election"
    expected_link = f"https://discord.com/channels/123/{settings.nom_channel_id}/99"
    assert embed_entry.fields[0]["name"] == f"1. [Book]({expected_link})"
    assert embed_entry.fields[0]["value"].endswith("...")
    assert interaction.followup.messages[0]["content"] == "Election opened."


@pytest.mark.asyncio
async def test_close_voting_handles_missing(monkeypatch):
    interaction = DummyInteraction()
    session = DummySession()
    vs = VotingSession(bot=SimpleNamespace())
    monkeypatch.setattr(
        "bot.commands.voting_session.async_session", lambda: session_cm(session)
    )
    monkeypatch.setattr(
        "bot.commands.voting_session.get_open_election", AsyncMock(return_value=None)
    )

    await vs.close_voting(interaction)

    assert interaction.response.messages[0]["content"] == "No open election found."


@pytest.mark.asyncio
async def test_close_voting_announces_result(monkeypatch):
    interaction = DummyInteraction()
    session = DummySession()
    vs = VotingSession(bot=SimpleNamespace())
    election = SimpleNamespace()
    monkeypatch.setattr(
        "bot.commands.voting_session.async_session", lambda: session_cm(session)
    )
    monkeypatch.setattr(
        "bot.commands.voting_session.get_open_election",
        AsyncMock(return_value=election),
    )
    monkeypatch.setattr(
        "bot.commands.voting_session.close_and_tally",
        AsyncMock(return_value=SimpleNamespace()),
    )

    await vs.close_voting(interaction)

    assert (
        interaction.response.messages[0]["content"]
        == "Election closed and results announced."
    )


@pytest.mark.asyncio
async def test_close_voting_handles_no_votes(monkeypatch):
    interaction = DummyInteraction()
    session = DummySession()
    vs = VotingSession(bot=SimpleNamespace())
    election = SimpleNamespace()
    monkeypatch.setattr(
        "bot.commands.voting_session.async_session", lambda: session_cm(session)
    )
    monkeypatch.setattr(
        "bot.commands.voting_session.get_open_election",
        AsyncMock(return_value=election),
    )
    monkeypatch.setattr(
        "bot.commands.voting_session.close_and_tally", AsyncMock(return_value=None)
    )

    await vs.close_voting(interaction)

    assert interaction.response.messages[0]["content"] == "No votes were cast."


@pytest.mark.asyncio
async def test_ballot_preview_requires_no_open_election(monkeypatch):
    interaction = DummyInteraction()
    session = DummySession()
    vs = VotingSession(bot=SimpleNamespace())
    monkeypatch.setattr(
        "bot.commands.voting_session.async_session", lambda: session_cm(session)
    )
    monkeypatch.setattr(
        "bot.commands.voting_session.get_open_election",
        AsyncMock(return_value=SimpleNamespace()),
    )

    await vs.ballot_preview(interaction)

    assert (
        interaction.followup.messages[0]["content"]
        == "An election is currently open. Cannot preview ballot."
    )


@pytest.mark.asyncio
async def test_ballot_preview_sends_embed(monkeypatch):
    session = DummySession(
        execute_results=[
            DummyResult(
                scalars=[
                    SimpleNamespace(book_id=1, message_id=101),
                    SimpleNamespace(book_id=2, message_id=202),
                ]
            ),
            DummyResult(
                scalars=[
                    SimpleNamespace(id=1, title="Book One", summary="Summary"),
                    SimpleNamespace(id=2, title="Book Two", summary="Details"),
                ]
            ),
        ]
    )
    monkeypatch.setattr(
        "bot.commands.voting_session.async_session", lambda: session_cm(session)
    )
    monkeypatch.setattr(
        "bot.commands.voting_session.get_open_election", AsyncMock(return_value=None)
    )
    vs = VotingSession(bot=SimpleNamespace())
    monkeypatch.setattr(
        vs, "get_top_noms", AsyncMock(return_value=[(1, 2, 1.0, 3.0), (2, 1, 0.5, 1.5)])
    )
    interaction = DummyInteraction()
    interaction.guild_id = 123

    await vs.ballot_preview(interaction, limit=2)

    embed = interaction.followup.messages[0]["embed"]
    assert embed.title == "Upcoming Ballot Preview"
    expected_preview_link = (
        f"https://discord.com/channels/123/{settings.nom_channel_id}/101"
    )
    assert embed.fields[0]["name"] == f"1. [Book One]({expected_preview_link})"
    assert "Score" in embed.fields[0]["value"]


@pytest.mark.asyncio
async def test_get_reacts_for_nomination_counts_unique(monkeypatch):
    from discord.ext import commands

    class DummyUsers:
        def __init__(self, ids):
            self.ids = ids

        def __aiter__(self):
            async def generator():
                for uid in self.ids:
                    yield SimpleNamespace(id=uid)

            return generator()

    class DummyReaction:
        def __init__(self, ids, emoji):
            self._ids = ids
            self.emoji = emoji

        def users(self):
            return DummyUsers(self._ids)

    class DummyMessage:
        def __init__(self):
            self.reactions = [
                DummyReaction([1, 2, 3], "👍"),
                DummyReaction([2, 4], "🔥"),
                DummyReaction([99], NOMINATION_CANCEL_EMOJI),
            ]

    class ReactChannel(DummyChannel):
        async def fetch_message(self, _message_id):
            return DummyMessage()

    bot = commands.Bot()
    channel = ReactChannel(settings.nom_channel_id)
    bot.add_channel(settings.nom_channel_id, channel)
    nomination = SimpleNamespace(message_id=10, nominator_discord_id=2)
    vs = VotingSession(bot=bot)

    count = await vs.get_reacts_for_nomination(nomination)

    assert count == 3


@pytest.mark.asyncio
async def test_update_all_nominations_refreshes_data(monkeypatch):
    nomination = SimpleNamespace(reactions=0)
    session = DummySession(execute_results=[DummyResult(scalars=[nomination])])
    vs = VotingSession(bot=SimpleNamespace())
    monkeypatch.setattr(vs, "get_reacts_for_nomination", AsyncMock(return_value=5))

    await vs.update_all_nominations(session)

    assert nomination.reactions == 5
    assert session.commit_calls == 1


@pytest.mark.asyncio
async def test_ballot_preview_handles_empty_ballot(monkeypatch):
    session = DummySession(execute_results=[DummyResult()])
    monkeypatch.setattr(
        "bot.commands.voting_session.async_session", lambda: session_cm(session)
    )
    monkeypatch.setattr(
        "bot.commands.voting_session.get_open_election", AsyncMock(return_value=None)
    )
    vs = VotingSession(bot=SimpleNamespace())
    monkeypatch.setattr(vs, "get_top_noms", AsyncMock(return_value=[]))
    interaction = DummyInteraction()

    await vs.ballot_preview(interaction)

    assert (
        interaction.followup.messages[0]["content"]
        == "No nominations available for voting."
    )
