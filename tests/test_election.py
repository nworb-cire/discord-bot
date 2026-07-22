# tests/test_election.py
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.dialects import postgresql

from bot import election as election_mod
from tests.utils import DummyResult


def test_get_election_vote_totals_groups_only_vote_book_ids():
    async def _run():
        session = SimpleNamespace(execute=AsyncMock(return_value=DummyResult(rows=[])))

        assert await election_mod.get_election_vote_totals(session, 7) == []

        stmt = session.execute.await_args.args[0]
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        assert "GROUP BY votes.book_id" in sql
        assert "GROUP BY books." not in sql

    asyncio.run(_run())


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
        assert session.commit.await_count == 1

        embed = channel.send.await_args.kwargs["embed"]
        assert embed.title == "Election Results"
        assert embed.fields[0]["name"] == "Winner"
        assert embed.fields[0]["value"] == book.title
        assert embed.fields[1]["name"].startswith("1.")
        assert embed.fields[1]["value"].startswith("Votes: 3.5")

    asyncio.run(_run())


def test_close_and_tally_fetches_uncached_channel(monkeypatch):
    async def _run():
        election = SimpleNamespace(id=7, winner=None)
        channel = SimpleNamespace(send=AsyncMock())
        client = SimpleNamespace(
            get_channel=lambda _: None,
            fetch_channel=AsyncMock(return_value=channel),
        )
        session = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock())

        monkeypatch.setattr(
            election_mod, "settings", SimpleNamespace(bookclub_channel_id=99)
        )
        monkeypatch.setattr(
            election_mod,
            "get_election_vote_totals",
            AsyncMock(return_value=[]),
        )

        await election_mod.close_and_tally(client, session, election)

        session.commit.assert_awaited_once()
        client.fetch_channel.assert_awaited_once_with(99)
        channel.send.assert_awaited_once()

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
