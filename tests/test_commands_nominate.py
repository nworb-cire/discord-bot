import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.config import get_settings
from bot.commands.nominate import (
    LOOKUP_ERROR_MESSAGE,
    BookLookupError,
    BookLookupResult,
    Nominate,
)
from bot.db import Book, Nomination
from bot.utils import NOMINATION_CANCEL_EMOJI, utcnow
from tests.utils import (
    DummyChannel,
    DummyInteraction,
    DummyResult,
    DummySession,
    session_cm,
)

settings = get_settings()


async def flush_tasks():
    await asyncio.sleep(0)
    await asyncio.sleep(0)


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


def lookup_result(**overrides):
    data = {
        "title": "The Title",
        "subtitle": "An Adventure",
        "authors": ["The Author"],
        "isbn_10": "0395193958",
        "isbn_13": "9780395193952",
        "description": "Fun book",
        "summary": "Great book",
        "page_count": 321,
    }
    data.update(overrides)
    return BookLookupResult(**data)


@pytest.mark.asyncio
async def test_nominate_existing_book(monkeypatch):
    existing_book = SimpleNamespace(title="Existing", id=1)
    existing_nomination = SimpleNamespace(book_id=1)
    session = DummySession(
        execute_results=[
            DummyResult(scalar=existing_book),
            DummyResult(scalar=existing_nomination),
        ]
    )
    monkeypatch.setattr(
        "bot.commands.nominate.async_session", lambda: session_cm(session)
    )
    interaction = DummyInteraction()
    cog = Nominate(bot=SimpleNamespace())
    monkeypatch.setattr(cog, "lookup_book", AsyncMock(return_value=lookup_result()))

    await cog.nominate(interaction, "978-0-395-19395-2")

    assert interaction.response.deferred is True
    assert interaction.followup.messages[0]["content"].startswith("*Existing*")
    assert session.commit_calls == 0


@pytest.mark.asyncio
async def test_nominate_matching_book_without_nomination_reuses_book(monkeypatch):
    fixed_now = utcnow()
    existing_book = SimpleNamespace(
        id=42,
        title="Existing",
        summary="Existing summary",
        length=111,
    )
    session = DummySession(
        execute_results=[
            DummyResult(scalar=existing_book),
            DummyResult(scalar=None),
        ]
    )
    monkeypatch.setattr(
        "bot.commands.nominate.async_session", lambda: session_cm(session)
    )
    monkeypatch.setattr("bot.commands.nominate.utcnow", lambda: fixed_now)

    cog = Nominate(bot=SimpleNamespace())
    monkeypatch.setattr(cog, "lookup_book", AsyncMock(return_value=lookup_result()))

    nom_channel = DummyChannel(2)
    interaction = DummyInteraction(
        user_id=99,
        client=SimpleNamespace(get_channel=lambda _cid: nom_channel),
    )

    await cog.nominate(interaction, "978-0-395-19395-2")

    assert interaction.followup.messages[-1]["content"] == "Nominated *Existing*"
    assert not any(isinstance(obj, Book) for obj in session.added)
    nomination = next(obj for obj in session.added if isinstance(obj, Nomination))
    assert nomination.book_id == existing_book.id
    assert nomination.nominator_discord_id == 99
    assert nomination.created_at == fixed_now
    assert nom_channel.messages[0]["embed"].title == "Existing"
    assert "Existing summary" in nom_channel.messages[0]["embed"].description
    assert session.commit_calls == 1


@pytest.mark.asyncio
async def test_nominate_creates_book_and_posts_embed(monkeypatch):
    fixed_now = utcnow()

    async def commit_hook(session):
        for obj in session.added:
            if isinstance(obj, Book) and getattr(obj, "id", None) is None:
                obj.id = 42

    session = DummySession(
        execute_results=[DummyResult(scalar=None), DummyResult(scalars=[])],
        commit_hook=commit_hook,
    )
    monkeypatch.setattr(
        "bot.commands.nominate.async_session", lambda: session_cm(session)
    )
    monkeypatch.setattr("bot.commands.nominate.utcnow", lambda: fixed_now)

    cog = Nominate(bot=SimpleNamespace())
    monkeypatch.setattr(cog, "lookup_book", AsyncMock(return_value=lookup_result()))

    nom_channel = DummyChannel(2)
    interaction_channel = DummyChannel(5)
    interaction = DummyInteraction(
        channel_id=interaction_channel.id,
        user_id=99,
        client=SimpleNamespace(get_channel=lambda _cid: nom_channel),
    )
    interaction.channel = interaction_channel

    await cog.nominate(interaction, "The Title by The Author")

    assert interaction.followup.messages[-1]["content"].startswith("Nominated")
    assert session.commit_calls == 1
    assert session.flush_calls >= 2
    embed_entry = nom_channel.messages[0]
    assert embed_entry["embed"].title == "The Title: An Adventure"
    assert "Great book" in embed_entry["embed"].description
    assert "Note: AI generated" not in embed_entry["embed"].description
    assert NOMINATION_CANCEL_EMOJI in embed_entry["reactions"]
    assert interaction.channel.messages[0]["content"].startswith("<@99> nominated")
    book = next(obj for obj in session.added if isinstance(obj, Book))
    assert not hasattr(book, "isbn")
    assert book.isbn_10 == "0395193958"
    assert book.isbn_13 == "9780395193952"
    assert book.authors == ["The Author"]
    assert book.primary_author == "The Author"
    assert book.length == 321
    nomination = next(obj for obj in session.added if isinstance(obj, Nomination))
    assert nomination.message_id == 1


@pytest.mark.asyncio
async def test_cancel_reaction_by_nominator_deletes_nomination(monkeypatch):
    nomination_row = SimpleNamespace(book_id=7, nominator_discord_id=88, message_id=1)
    book_row = SimpleNamespace(id=7)
    session = DummySession(
        execute_results=[DummyResult(scalar=nomination_row)],
        get_results={7: book_row},
    )
    monkeypatch.setattr(
        "bot.commands.nominate.async_session", lambda: session_cm(session)
    )

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
    nomination_row = SimpleNamespace(book_id=7, nominator_discord_id=88, message_id=1)
    session = DummySession(execute_results=[DummyResult(scalar=nomination_row)])
    monkeypatch.setattr(
        "bot.commands.nominate.async_session", lambda: session_cm(session)
    )

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
async def test_non_cancel_reaction_refreshes_nomination_count(monkeypatch):
    nomination_row = SimpleNamespace(
        book_id=7, nominator_discord_id=88, message_id=1, reactions=0
    )
    session = DummySession(execute_results=[DummyResult(scalar=nomination_row)])
    monkeypatch.setattr(
        "bot.commands.nominate.async_session", lambda: session_cm(session)
    )
    real_sleep = asyncio.sleep

    async def immediate_sleep(_delay):
        await real_sleep(0)

    monkeypatch.setattr("bot.commands.nominate.asyncio.sleep", immediate_sleep)

    class ReactionMessage:
        def __init__(self):
            self.reactions = [
                DummyReaction([88, 101], "👍"),
                DummyReaction([101, 202], "🔥"),
                DummyReaction([303], NOMINATION_CANCEL_EMOJI),
            ]

    channel = DummyChannel(settings.nom_channel_id)

    async def fetch_message(_message_id):
        return ReactionMessage()

    channel.fetch_message = fetch_message

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
        user_id=101,
        emoji="👍",
    )

    await cog.on_raw_reaction_add(payload)
    await flush_tasks()

    assert nomination_row.reactions == 2
    assert session.commit_calls == 1


@pytest.mark.asyncio
async def test_reaction_remove_refreshes_nomination_count(monkeypatch):
    nomination_row = SimpleNamespace(
        book_id=7, nominator_discord_id=88, message_id=1, reactions=3
    )
    session = DummySession(execute_results=[DummyResult(scalar=nomination_row)])
    monkeypatch.setattr(
        "bot.commands.nominate.async_session", lambda: session_cm(session)
    )
    real_sleep = asyncio.sleep

    async def immediate_sleep(_delay):
        await real_sleep(0)

    monkeypatch.setattr("bot.commands.nominate.asyncio.sleep", immediate_sleep)

    class ReactionMessage:
        def __init__(self):
            self.reactions = [DummyReaction([202], "👍")]

    channel = DummyChannel(settings.nom_channel_id)

    async def fetch_message(_message_id):
        return ReactionMessage()

    channel.fetch_message = fetch_message

    bot = SimpleNamespace(
        get_channel=lambda _cid: channel,
        fetch_channel=AsyncMock(return_value=channel),
        user=SimpleNamespace(id=999),
    )
    cog = Nominate(bot=bot)

    payload = SimpleNamespace(
        channel_id=settings.nom_channel_id,
        message_id=1,
        user_id=101,
        emoji="👍",
    )

    await cog.on_raw_reaction_remove(payload)
    await flush_tasks()

    assert nomination_row.reactions == 1
    assert session.commit_calls == 1


@pytest.mark.asyncio
async def test_nominator_reaction_does_not_change_second_count(monkeypatch):
    nomination_row = SimpleNamespace(
        book_id=7, nominator_discord_id=88, message_id=1, reactions=2
    )
    session = DummySession(execute_results=[DummyResult(scalar=nomination_row)])
    monkeypatch.setattr(
        "bot.commands.nominate.async_session", lambda: session_cm(session)
    )
    real_sleep = asyncio.sleep

    async def immediate_sleep(_delay):
        await real_sleep(0)

    monkeypatch.setattr("bot.commands.nominate.asyncio.sleep", immediate_sleep)

    channel = DummyChannel(settings.nom_channel_id)

    class ReactionMessage:
        def __init__(self):
            self.reactions = [DummyReaction([88, 101, 202], "👍")]

    fetch_mock = AsyncMock(return_value=ReactionMessage())
    channel.fetch_message = fetch_mock

    bot = SimpleNamespace(
        get_channel=lambda _cid: channel,
        fetch_channel=AsyncMock(return_value=channel),
        user=SimpleNamespace(id=999),
    )
    cog = Nominate(bot=bot)

    payload = SimpleNamespace(
        channel_id=settings.nom_channel_id,
        message_id=1,
        user_id=88,
        emoji="👍",
    )

    await cog.on_raw_reaction_add(payload)
    await flush_tasks()

    assert nomination_row.reactions == 2
    assert session.commit_calls == 1
    fetch_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_reaction_refresh_is_debounced(monkeypatch):
    cog = Nominate(bot=SimpleNamespace())
    real_sleep = asyncio.sleep

    async def immediate_sleep(_delay):
        await real_sleep(0)

    monkeypatch.setattr("bot.commands.nominate.asyncio.sleep", immediate_sleep)
    refresh_mock = AsyncMock()
    monkeypatch.setattr(cog, "_refresh_nomination_reactions", refresh_mock)

    payload = SimpleNamespace(
        channel_id=settings.nom_channel_id,
        message_id=1,
        user_id=101,
        emoji="👍",
    )

    await cog.on_raw_reaction_add(payload)
    await cog.on_raw_reaction_add(payload)
    await cog.on_raw_reaction_remove(payload)
    await flush_tasks()

    refresh_mock.assert_awaited_once_with(settings.nom_channel_id, 1)


@pytest.mark.asyncio
async def test_nominate_handles_missing_channel(monkeypatch):
    fixed_now = utcnow()

    async def commit_hook(session):
        for obj in session.added:
            if isinstance(obj, Book) and getattr(obj, "id", None) is None:
                obj.id = 55

    session = DummySession(
        execute_results=[DummyResult(scalar=None), DummyResult(scalars=[])],
        commit_hook=commit_hook,
    )
    monkeypatch.setattr(
        "bot.commands.nominate.async_session", lambda: session_cm(session)
    )
    monkeypatch.setattr("bot.commands.nominate.utcnow", lambda: fixed_now)

    cog = Nominate(bot=SimpleNamespace())
    monkeypatch.setattr(
        cog,
        "lookup_book",
        AsyncMock(
            return_value=lookup_result(
                title="Missing Channel Book",
                subtitle=None,
                summary="Summary",
            )
        ),
    )

    interaction = DummyInteraction(
        client=SimpleNamespace(get_channel=lambda _cid: None)
    )

    await cog.nominate(interaction, "0-395-19395-8")

    assert (
        interaction.followup.messages[-1]["content"]
        == "Unable to locate the nominations channel. Please contact an admin."
    )
    assert not session.deleted
    assert session.commit_calls == 0
    assert session.rollback_calls == 1


@pytest.mark.asyncio
async def test_nominate_handles_openai_lookup_error(monkeypatch):
    session = DummySession()
    monkeypatch.setattr(
        "bot.commands.nominate.async_session", lambda: session_cm(session)
    )
    cog = Nominate(bot=SimpleNamespace())
    monkeypatch.setattr(cog, "lookup_book", AsyncMock(side_effect=BookLookupError()))
    interaction = DummyInteraction()

    await cog.nominate(interaction, "Common Sense")

    assert interaction.followup.messages[0]["content"] == LOOKUP_ERROR_MESSAGE
    assert session.commit_calls == 0


@pytest.mark.asyncio
async def test_nominate_title_only_without_isbn_dedupes_by_title_and_author(
    monkeypatch,
):
    existing_book = SimpleNamespace(
        title="Common Sense",
        primary_author="Thomas Paine",
        id=9,
    )
    session = DummySession(
        execute_results=[
            DummyResult(scalars=[existing_book]),
            DummyResult(scalar=SimpleNamespace(book_id=9)),
        ]
    )
    monkeypatch.setattr(
        "bot.commands.nominate.async_session", lambda: session_cm(session)
    )
    cog = Nominate(bot=SimpleNamespace())
    monkeypatch.setattr(
        cog,
        "lookup_book",
        AsyncMock(
            return_value=lookup_result(
                title=" Common   Sense ",
                subtitle=None,
                authors=["thomas paine"],
                isbn_10=None,
                isbn_13=None,
            )
        ),
    )
    interaction = DummyInteraction()

    await cog.nominate(interaction, "Common Sense")

    assert interaction.followup.messages[0]["content"].startswith("*Common Sense*")
    assert session.commit_calls == 0


@pytest.mark.asyncio
async def test_nominate_title_only_without_isbn_creates_book(monkeypatch):
    fixed_now = utcnow()

    async def commit_hook(session):
        for obj in session.added:
            if isinstance(obj, Book) and getattr(obj, "id", None) is None:
                obj.id = 84

    session = DummySession(
        execute_results=[DummyResult(scalars=[])],
        commit_hook=commit_hook,
    )
    monkeypatch.setattr(
        "bot.commands.nominate.async_session", lambda: session_cm(session)
    )
    monkeypatch.setattr("bot.commands.nominate.utcnow", lambda: fixed_now)
    cog = Nominate(bot=SimpleNamespace())
    monkeypatch.setattr(
        cog,
        "lookup_book",
        AsyncMock(
            return_value=lookup_result(
                title="Common Sense",
                subtitle=None,
                authors=["Thomas Paine"],
                isbn_10=None,
                isbn_13=None,
                page_count=None,
            )
        ),
    )

    nom_channel = DummyChannel(2)
    interaction = DummyInteraction(
        client=SimpleNamespace(get_channel=lambda _cid: nom_channel)
    )

    await cog.nominate(interaction, "Common Sense")

    book = next(obj for obj in session.added if isinstance(obj, Book))
    assert book.title == "Common Sense"
    assert not hasattr(book, "isbn")
    assert book.isbn_10 is None
    assert book.isbn_13 is None
    assert book.primary_author == "Thomas Paine"
    assert book.length is None
    assert session.commit_calls == 1


@pytest.mark.asyncio
async def test_find_duplicate_checks_title_author_after_empty_isbn_match():
    cog = Nominate(bot=SimpleNamespace())
    existing_book = SimpleNamespace(
        title="The Title: An Adventure",
        primary_author="The Author",
        id=10,
    )
    session = DummySession(
        execute_results=[
            DummyResult(scalar=None),
            DummyResult(scalars=[existing_book]),
        ]
    )

    duplicate = await cog._find_duplicate_book(session, lookup_result())

    assert duplicate is existing_book


@pytest.mark.asyncio
async def test_find_duplicate_checks_structured_isbn_fields_only():
    cog = Nominate(bot=SimpleNamespace())
    isbn_match = SimpleNamespace(
        title="Different Title",
        primary_author="Different Author",
        id=10,
        isbn_10="0395193958",
        isbn_13=None,
    )
    session = DummySession(execute_results=[DummyResult(scalar=isbn_match)])

    duplicate = await cog._find_duplicate_book(session, lookup_result())

    assert duplicate is isbn_match
    statement = str(session.executed[0])
    assert "books.isbn_10" in statement
    assert "books.isbn_13" in statement
    assert "books.isbn IN" not in statement


def test_book_model_has_no_legacy_isbn_column():
    assert "isbn" not in Book.__table__.columns


@pytest.mark.asyncio
async def test_lookup_book_uses_openai_structured_output(monkeypatch):
    parsed = lookup_result(title="Common Sense", subtitle=None)
    parse_mock = AsyncMock(return_value=SimpleNamespace(output_parsed=parsed))

    class DummyOpenAI:
        def __init__(self, *, api_key):
            self.api_key = api_key
            self.responses = SimpleNamespace(parse=parse_mock)

    monkeypatch.setattr("bot.commands.nominate.AsyncOpenAI", DummyOpenAI)
    cog = Nominate(bot=SimpleNamespace())

    result = await cog.lookup_book("Common Sense")

    assert result is parsed
    parse_mock.assert_awaited_once()
    kwargs = parse_mock.await_args.kwargs
    assert kwargs["model"] == settings.openai_book_lookup_model
    assert kwargs["text_format"] is BookLookupResult
    assert kwargs["tools"] == [{"type": "web_search", "search_context_size": "medium"}]
    assert kwargs["reasoning"] == {
        "effort": settings.openai_book_lookup_reasoning_effort
    }
    assert kwargs["max_output_tokens"] == settings.openai_book_lookup_max_output_tokens


@pytest.mark.asyncio
async def test_lookup_book_rejects_invalid_structured_output(monkeypatch):
    parse_mock = AsyncMock(return_value=SimpleNamespace(output_parsed=None))

    class DummyOpenAI:
        def __init__(self, *, api_key):
            self.responses = SimpleNamespace(parse=parse_mock)

    monkeypatch.setattr("bot.commands.nominate.AsyncOpenAI", DummyOpenAI)
    cog = Nominate(bot=SimpleNamespace())

    with pytest.raises(BookLookupError):
        await cog.lookup_book("Common Sense")


@pytest.mark.asyncio
async def test_lookup_book_rejects_incomplete_response(monkeypatch):
    parse_mock = AsyncMock(
        return_value=SimpleNamespace(
            output_parsed=None,
            status="incomplete",
            incomplete_details=SimpleNamespace(reason="max_output_tokens"),
        )
    )

    class DummyOpenAI:
        def __init__(self, *, api_key):
            self.responses = SimpleNamespace(parse=parse_mock)

    monkeypatch.setattr("bot.commands.nominate.AsyncOpenAI", DummyOpenAI)
    cog = Nominate(bot=SimpleNamespace())

    with pytest.raises(BookLookupError, match="incomplete"):
        await cog.lookup_book("Common Sense")


def test_lookup_result_rejects_malformed_isbn():
    with pytest.raises(ValueError):
        lookup_result(isbn_13="123")


def test_lookup_result_requires_author():
    with pytest.raises(ValueError):
        lookup_result(authors=[])
