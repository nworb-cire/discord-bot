from types import SimpleNamespace

import httpx
import pytest

from bot.config import get_settings
from bot.commands.nominate import Nominate
from bot.db import Book, Nomination
from bot.utils import NOMINATION_CANCEL_EMOJI, utcnow
from tests.utils import DummyChannel, DummyInteraction, DummyResult, DummySession, session_cm

settings = get_settings()


class FakeOpenAI:
    def __init__(self, **_kwargs):
        self.responses = SimpleNamespace(create=None)


@pytest.mark.asyncio
async def test_nominate_existing_book(monkeypatch):
    monkeypatch.setattr("bot.commands.nominate.openai.AsyncOpenAI", lambda **kwargs: FakeOpenAI(**kwargs))
    existing_book = SimpleNamespace(title="Existing", id=1)
    session = DummySession(execute_results=[DummyResult(scalar=existing_book)])
    monkeypatch.setattr("bot.commands.nominate.async_session", lambda: session_cm(session))
    interaction = DummyInteraction()
    cog = Nominate(bot=SimpleNamespace())

    await cog.nominate(interaction, "978-1-234567-89-7")

    assert interaction.response.deferred is True
    assert interaction.followup.messages[0]["content"].startswith("Book with ISBN")
    assert session.commit_calls == 0


@pytest.mark.asyncio
async def test_nominate_creates_book_and_posts_embed(monkeypatch):
    monkeypatch.setattr("bot.commands.nominate.openai.AsyncOpenAI", lambda **kwargs: FakeOpenAI(**kwargs))
    summary_text = "Great book"
    fixed_now = utcnow()

    async def commit_hook(session):
        for obj in session.added:
            if isinstance(obj, Book) and getattr(obj, "id", None) is None:
                obj.id = 42

    session = DummySession(execute_results=[DummyResult(scalar=None)], commit_hook=commit_hook)
    monkeypatch.setattr("bot.commands.nominate.async_session", lambda: session_cm(session))
    monkeypatch.setattr("bot.commands.nominate.utcnow", lambda: fixed_now)

    cog = Nominate(bot=SimpleNamespace())
    async def fake_search(_isbn):
        return {
            "title": "The Title",
            "subtitle": "An Adventure",
            "description": "Fun book",
            "number_of_pages": 321,
        }

    async def fake_summarize(_title, _desc):
        return summary_text

    monkeypatch.setattr(cog, "open_library_search", fake_search)
    monkeypatch.setattr(cog, "openai_summarize", fake_summarize)

    nom_channel = DummyChannel(2)
    interaction_channel = DummyChannel(5)
    interaction = DummyInteraction(
        channel_id=interaction_channel.id,
        user_id=99,
        client=SimpleNamespace(get_channel=lambda _cid: nom_channel),
    )
    interaction.channel = interaction_channel

    await cog.nominate(interaction, "0-395-19395-8")

    assert interaction.followup.messages[-1]["content"].startswith("Nominated")
    assert session.commit_calls >= 3
    embed_entry = nom_channel.messages[0]
    assert embed_entry["embed"].title == "The Title: An Adventure"
    assert "Note: AI generated" in embed_entry["embed"].description
    assert NOMINATION_CANCEL_EMOJI in embed_entry["reactions"]
    assert interaction.channel.messages[0]["content"].startswith("<@99> nominated")
    nomination = next(obj for obj in session.added if isinstance(obj, Nomination))
    assert nomination.message_id == 1


@pytest.mark.asyncio
async def test_cancel_reaction_by_nominator_deletes_nomination(monkeypatch):
    monkeypatch.setattr("bot.commands.nominate.openai.AsyncOpenAI", lambda **kwargs: FakeOpenAI(**kwargs))

    nomination_row = SimpleNamespace(book_id=7, nominator_discord_id=88, message_id=1)
    book_row = SimpleNamespace(id=7)
    session = DummySession(
        execute_results=[DummyResult(scalar=nomination_row)],
        get_results={7: book_row},
    )
    monkeypatch.setattr("bot.commands.nominate.async_session", lambda: session_cm(session))

    channel = DummyChannel(settings.nom_channel_id)
    await channel.send(content="Nomination")

    async def fetch_channel(_cid):
        return channel

    bot = SimpleNamespace(
        get_channel=lambda _cid: channel,
        fetch_channel=fetch_channel,
        user=SimpleNamespace(id=999),
    )
    cog = Nominate(bot=bot)

    payload = SimpleNamespace(
        channel_id=settings.nom_channel_id,
        message_id=1,
        user_id=88,
        emoji=NOMINATION_CANCEL_EMOJI,
    )

    await cog.on_raw_reaction_add(payload)

    message = await channel.fetch_message(1)
    assert message.deleted is True
    assert session.deleted == [nomination_row, book_row]
    assert session.commit_calls == 1


@pytest.mark.asyncio
async def test_cancel_reaction_ignored_for_other_users(monkeypatch):
    monkeypatch.setattr("bot.commands.nominate.openai.AsyncOpenAI", lambda **kwargs: FakeOpenAI(**kwargs))

    nomination_row = SimpleNamespace(book_id=7, nominator_discord_id=88, message_id=1)
    session = DummySession(execute_results=[DummyResult(scalar=nomination_row)])
    monkeypatch.setattr("bot.commands.nominate.async_session", lambda: session_cm(session))

    channel = DummyChannel(settings.nom_channel_id)
    await channel.send(content="Nomination")

    async def fetch_channel(_cid):
        return channel

    bot = SimpleNamespace(
        get_channel=lambda _cid: channel,
        fetch_channel=fetch_channel,
        user=SimpleNamespace(id=999),
    )
    cog = Nominate(bot=bot)

    payload = SimpleNamespace(
        channel_id=settings.nom_channel_id,
        message_id=1,
        user_id=42,
        emoji=NOMINATION_CANCEL_EMOJI,
    )

    await cog.on_raw_reaction_add(payload)

    message = await channel.fetch_message(1)
    assert message.deleted is False
    assert session.deleted == []
    assert session.commit_calls == 0


@pytest.mark.asyncio
async def test_nominate_handles_open_library_error(monkeypatch):
    monkeypatch.setattr("bot.commands.nominate.openai.AsyncOpenAI", lambda **kwargs: FakeOpenAI(**kwargs))
    request = httpx.Request("GET", "https://example.com")
    response = httpx.Response(500, request=request)
    error = httpx.HTTPStatusError("boom", request=request, response=response)
    session = DummySession(execute_results=[DummyResult(scalar=None)])
    monkeypatch.setattr("bot.commands.nominate.async_session", lambda: session_cm(session))
    cog = Nominate(bot=SimpleNamespace())

    async def failing(_isbn):
        raise error

    monkeypatch.setattr(cog, "open_library_search", failing)
    interaction = DummyInteraction()

    await cog.nominate(interaction, "1234567890")

    assert interaction.followup.messages[0]["content"] == "Failed to fetch book metadata from OpenLibrary.org."
    assert session.commit_calls == 0


@pytest.mark.asyncio
async def test_nominate_handles_missing_metadata(monkeypatch):
    monkeypatch.setattr("bot.commands.nominate.openai.AsyncOpenAI", lambda **kwargs: FakeOpenAI(**kwargs))
    session = DummySession(execute_results=[DummyResult(scalar=None)])
    monkeypatch.setattr("bot.commands.nominate.async_session", lambda: session_cm(session))
    cog = Nominate(bot=SimpleNamespace())

    async def missing(_isbn):
        return {}

    monkeypatch.setattr(cog, "open_library_search", missing)
    interaction = DummyInteraction()

    await cog.nominate(interaction, "1234567890")

    assert interaction.followup.messages[0]["content"] == "Failed to find book in OpenLibrary.org."
    assert session.commit_calls == 0


@pytest.mark.asyncio
async def test_open_library_search_returns_json(monkeypatch):
    class DummyResponse:
        def __init__(self):
            self._data = {"title": "Example"}

        def json(self):
            return self._data

        def raise_for_status(self):
            return None

    class DummyClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *_args, **_kwargs):
            return DummyResponse()

    monkeypatch.setattr("bot.commands.nominate.httpx.AsyncClient", DummyClient)
    monkeypatch.setattr("bot.commands.nominate.openai.AsyncOpenAI", lambda **kwargs: FakeOpenAI(**kwargs))
    cog = Nominate(bot=SimpleNamespace())

    data = await cog.open_library_search("123")

    assert data["title"] == "Example"


@pytest.mark.asyncio
async def test_openai_summarize_handles_empty_response(monkeypatch):
    monkeypatch.setattr("bot.commands.nominate.openai.AsyncOpenAI", lambda **kwargs: FakeOpenAI(**kwargs))
    cog = Nominate(bot=SimpleNamespace())

    class DummyResponses:
        async def create(self, **_kwargs):
            return SimpleNamespace(output=[])

    cog.openai_client = SimpleNamespace(responses=DummyResponses())

    result = await cog.openai_summarize("Title", "Desc")

    assert result == "No summary available."
