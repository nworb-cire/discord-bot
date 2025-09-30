from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.commands.vote import Ballot, BallotModal
from bot.config import get_settings
from tests.utils import DummyInteraction, DummyResult, DummySession, session_cm

settings = get_settings()


@pytest.mark.asyncio
async def test_record_votes_requires_open_election(monkeypatch):
    modal = BallotModal.__new__(BallotModal)
    modal.is_bookclub = False
    monkeypatch.setattr(
        "bot.commands.vote.get_open_election", AsyncMock(return_value=None)
    )
    session = DummySession()
    monkeypatch.setattr("bot.commands.vote.async_session", lambda: session_cm(session))

    update_mock = AsyncMock()
    monkeypatch.setattr("bot.commands.vote.update_election_vote_reaction", update_mock)
    interaction = SimpleNamespace(user=SimpleNamespace(id=1), client=SimpleNamespace())

    with pytest.raises(Exception) as excinfo:
        await BallotModal.record_votes(modal, interaction, entries={})

    assert "Voting is not currently open" in str(excinfo.value)
    update_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_record_votes_rejects_excess_total(monkeypatch):
    modal = BallotModal.__new__(BallotModal)
    modal.is_bookclub = False
    monkeypatch.setattr(
        "bot.commands.vote.get_open_election",
        AsyncMock(return_value=SimpleNamespace(id=1)),
    )
    session = DummySession()
    monkeypatch.setattr("bot.commands.vote.async_session", lambda: session_cm(session))

    update_mock = AsyncMock()
    monkeypatch.setattr("bot.commands.vote.update_election_vote_reaction", update_mock)
    interaction = SimpleNamespace(user=SimpleNamespace(id=1), client=SimpleNamespace())

    with pytest.raises(Exception) as excinfo:
        await BallotModal.record_votes(modal, interaction, entries={1: 3})

    assert "Total score exceeds" in str(excinfo.value)
    update_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_record_votes_persists(monkeypatch):
    modal = BallotModal.__new__(BallotModal)
    modal.is_bookclub = True
    monkeypatch.setattr(
        "bot.commands.vote.get_open_election",
        AsyncMock(return_value=SimpleNamespace(id=7)),
    )
    session = DummySession(execute_results=[DummyResult(), DummyResult()])
    monkeypatch.setattr("bot.commands.vote.async_session", lambda: session_cm(session))
    update_mock = AsyncMock()
    monkeypatch.setattr("bot.commands.vote.update_election_vote_reaction", update_mock)
    client = SimpleNamespace()
    interaction = SimpleNamespace(user=SimpleNamespace(id=9), client=client)

    election_id = await BallotModal.record_votes(
        modal, interaction, entries={1: 2.0, 2: 1.0}
    )

    assert len(session.executed) == 2
    assert session.commit_calls == 1
    update_mock.assert_awaited_once_with(client, election_id)


@pytest.mark.asyncio
async def test_on_submit_blocks_invalid_numbers(monkeypatch):
    modal = BallotModal([SimpleNamespace(id=1, title="A")])
    modal.children[0].value = "abc"
    modal.record_votes = AsyncMock()
    interaction = DummyInteraction()

    await modal.on_submit(interaction)

    assert interaction.response.messages[0]["content"] == "Numbers only."
    assert modal.record_votes.await_count == 0


@pytest.mark.asyncio
async def test_on_submit_records_votes(monkeypatch):
    modal = BallotModal(
        [SimpleNamespace(id=1, title="A"), SimpleNamespace(id=2, title="B")]
    )
    modal.children[0].value = "2"
    modal.children[1].value = "3"
    modal.record_votes = AsyncMock()
    interaction = DummyInteraction()

    await modal.on_submit(interaction)

    modal.record_votes.assert_awaited_once()
    assert modal.record_votes.await_args.args[0] is interaction
    assert modal.record_votes.await_args.args[1] == {1: 2.0, 2: 3.0}
    assert interaction.response.messages[-1]["content"] == "Votes recorded."


@pytest.mark.asyncio
async def test_vote_command_handles_no_election(monkeypatch):
    session = DummySession(execute_results=[DummyResult(scalar=None)])
    monkeypatch.setattr("bot.commands.vote.async_session", lambda: session_cm(session))
    interaction = DummyInteraction()
    ballot = Ballot(bot=SimpleNamespace())

    await ballot.vote(interaction)

    assert interaction.response.messages[0]["content"] == "Voting not open."


@pytest.mark.asyncio
async def test_vote_command_requires_nominations(monkeypatch):
    election = SimpleNamespace(ballot=[1, 2])
    session = DummySession(
        execute_results=[
            DummyResult(scalar=election),
            DummyResult(scalars=[]),
        ]
    )
    monkeypatch.setattr("bot.commands.vote.async_session", lambda: session_cm(session))
    interaction = DummyInteraction()
    ballot = Ballot(bot=SimpleNamespace())

    await ballot.vote(interaction)

    assert (
        interaction.response.messages[0]["content"]
        == "No nominations available for voting."
    )


@pytest.mark.asyncio
async def test_vote_command_opens_modal(monkeypatch):
    election = SimpleNamespace(ballot=[2, 1])
    books = [SimpleNamespace(id=1, title="One"), SimpleNamespace(id=2, title="Two")]
    session = DummySession(
        execute_results=[
            DummyResult(scalar=election),
            DummyResult(scalars=books),
        ]
    )
    monkeypatch.setattr("bot.commands.vote.async_session", lambda: session_cm(session))
    interaction = DummyInteraction(
        user_roles=[SimpleNamespace(id=settings.role_highweight_id)]
    )
    ballot = Ballot(bot=SimpleNamespace())

    await ballot.vote(interaction)

    modal = interaction.response.modals[0]
    assert isinstance(modal, BallotModal)
    assert modal.is_bookclub is True
    titles = [child.label for child in modal.children]
    assert titles[0] == "Two"


@pytest.mark.asyncio
async def test_on_submit_handles_float_error(monkeypatch):
    modal = BallotModal([SimpleNamespace(id=1, title="A")])
    modal.children[0].value = "2"
    modal.record_votes = AsyncMock()
    interaction = DummyInteraction()

    def bad_float(_value):
        raise ValueError("bad")

    monkeypatch.setattr("builtins.float", bad_float)

    await modal.on_submit(interaction)

    assert interaction.response.messages[0]["content"] == "Invalid number format."
    assert modal.record_votes.await_count == 0


@pytest.mark.asyncio
async def test_on_submit_handles_record_vote_error(monkeypatch):
    modal = BallotModal([SimpleNamespace(id=1, title="A")])
    modal.children[0].value = "1"
    modal.record_votes = AsyncMock(side_effect=Exception("boom"))
    interaction = DummyInteraction()

    await modal.on_submit(interaction)

    assert interaction.response.messages[0]["content"] == "boom"
