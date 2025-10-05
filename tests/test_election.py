# tests/test_election.py
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot import election as election_mod


def test_close_and_tally_announces_winner(monkeypatch):
    async def _run():
        book = SimpleNamespace(id=1, title="Book A")
        election = SimpleNamespace(id=42, winner=None)
        channel = SimpleNamespace(send=AsyncMock())
        client = SimpleNamespace(get_channel=lambda _: channel)
        session = SimpleNamespace()
        session.execute = AsyncMock(return_value=None)
        session.commit = AsyncMock()

        monkeypatch.setattr(
            election_mod, "utcnow", lambda: datetime(2024, 1, 1, tzinfo=timezone.utc)
        )
        monkeypatch.setattr(
            election_mod, "settings", SimpleNamespace(bookclub_channel_id=99)
        )
        monkeypatch.setattr(
            election_mod,
            "get_election_vote_totals",
            AsyncMock(return_value=[(book, 3.5)]),
        )

        winner = await election_mod.close_and_tally(
            client, session, election, closed_by=100
        )

        assert winner is book
        assert election.winner == book.id
        assert session.execute.await_count == 1
        assert session.commit.await_count == 2

        embed = channel.send.await_args.kwargs["embed"]
        assert embed.title == "Election Results"
        assert embed.fields[0]["name"] == "Winner"
        assert embed.fields[0]["value"] == book.title
        assert embed.fields[1]["name"].startswith("1.")
        assert embed.fields[1]["value"].startswith("Votes: 3.5")

    asyncio.run(_run())


def test_close_and_tally_handles_no_votes(monkeypatch):
    async def _run():
        election = SimpleNamespace(id=7, winner=None)
        channel = SimpleNamespace(send=AsyncMock())
        client = SimpleNamespace(get_channel=lambda _: channel)
        session = SimpleNamespace()
        session.execute = AsyncMock(return_value=None)
        session.commit = AsyncMock()

        monkeypatch.setattr(
            election_mod, "utcnow", lambda: datetime(2024, 1, 1, tzinfo=timezone.utc)
        )
        monkeypatch.setattr(
            election_mod, "settings", SimpleNamespace(bookclub_channel_id=99)
        )
        monkeypatch.setattr(
            election_mod,
            "get_election_vote_totals",
            AsyncMock(return_value=[]),
        )

        winner = await election_mod.close_and_tally(client, session, election)

        assert winner is None
        assert election.winner is None
        assert session.commit.await_count == 1

        embed = channel.send.await_args.kwargs["embed"]
        assert embed.fields[0]["value"] == "None"

    asyncio.run(_run())
