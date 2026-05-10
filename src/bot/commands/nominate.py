import asyncio
import re
from contextlib import suppress
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands
from loguru import logger
from openai import AsyncOpenAI, OpenAIError
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import func, or_, select

from bot.config import get_settings
from bot.db import async_session, Book, Nomination
from bot.utils import (
    NOMINATION_CANCEL_EMOJI,
    UserFacingError,
    handle_interaction_errors,
    utcnow,
)

settings = get_settings()
ISBN_RE = re.compile(r"[^0-9Xx]")
LOOKUP_ERROR_MESSAGE = (
    "Failed to look up book details with OpenAI. Please try again later."
)


class BookLookupError(Exception):
    pass


class BookLookupResult(BaseModel):
    title: str
    subtitle: str | None = None
    authors: list[str] = Field(default_factory=list)
    isbn_10: str | None = None
    isbn_13: str | None = None
    description: str | None = None
    summary: str
    page_count: int | None = None

    @field_validator("title", "summary")
    @classmethod
    def _require_text(cls, value: str) -> str:
        value = " ".join(str(value or "").split())
        if not value:
            raise ValueError("value must not be empty")
        return value

    @field_validator("subtitle", "description")
    @classmethod
    def _normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = " ".join(str(value).split())
        return value or None

    @field_validator("authors")
    @classmethod
    def _require_authors(cls, value: list[str]) -> list[str]:
        authors = [" ".join(str(author).split()) for author in value]
        authors = [author for author in authors if author]
        if not authors:
            raise ValueError("at least one author is required")
        return authors

    @field_validator("isbn_10", "isbn_13")
    @classmethod
    def _normalize_isbn(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        normalized = ISBN_RE.sub("", str(value)).upper()
        if not normalized:
            return None
        expected_length = 10 if info.field_name == "isbn_10" else 13
        if len(normalized) != expected_length:
            raise ValueError(f"{info.field_name} must be {expected_length} characters")
        return normalized

    @field_validator("page_count")
    @classmethod
    def _normalize_page_count(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if value <= 0:
            return None
        return value

    @property
    def full_title(self) -> str:
        return f"{self.title}: {self.subtitle}" if self.subtitle else self.title

    @property
    def preferred_isbn(self) -> str | None:
        return self.isbn_13 or self.isbn_10


class Nominate(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._reaction_refresh_tasks: dict[int, asyncio.Task[None]] = {}

    async def _get_nomination_channel(self, channel_id: int):
        channel = None
        if hasattr(self.bot, "get_channel"):
            channel = self.bot.get_channel(channel_id)
        if channel is None and hasattr(self.bot, "fetch_channel"):
            channel = await self.bot.fetch_channel(channel_id)
        return channel

    @staticmethod
    async def _count_nomination_reactions(
        message: Any, *, exclude_user_id: int | None = None
    ) -> int:
        unique_users: set[int] = set()
        for reaction in getattr(message, "reactions", []):
            emoji = getattr(reaction, "emoji", reaction)
            if str(emoji) == NOMINATION_CANCEL_EMOJI:
                continue
            async for user in reaction.users():
                unique_users.add(user.id)
        if exclude_user_id is not None:
            unique_users.discard(exclude_user_id)
        return len(unique_users)

    async def _refresh_nomination_reactions(
        self, channel_id: int, message_id: int
    ) -> None:
        async with async_session() as session:
            stmt = select(Nomination).where(Nomination.message_id == message_id)
            nomination = (await session.execute(stmt)).scalar_one_or_none()
            if nomination is None:
                return
            channel = await self._get_nomination_channel(channel_id)
            if channel is None or not hasattr(channel, "fetch_message"):
                return
            with suppress(Exception):
                message = await channel.fetch_message(message_id)
                nomination.reactions = await self._count_nomination_reactions(
                    message, exclude_user_id=nomination.nominator_discord_id
                )
                session.add(nomination)
                await session.commit()

    async def _debounced_refresh_nomination_reactions(
        self, channel_id: int, message_id: int
    ) -> None:
        delay = max(0.0, settings.nomination_reaction_refresh_debounce_seconds)
        try:
            if delay:
                await asyncio.sleep(delay)
            await self._refresh_nomination_reactions(channel_id, message_id)
        except Exception:
            logger.exception(
                "Failed to refresh nomination reactions for message {}",
                message_id,
            )
        finally:
            self._reaction_refresh_tasks.pop(message_id, None)

    def _schedule_nomination_reaction_refresh(
        self, channel_id: int, message_id: int
    ) -> None:
        existing_task = self._reaction_refresh_tasks.get(message_id)
        if existing_task is not None and not existing_task.done():
            return
        self._reaction_refresh_tasks[message_id] = asyncio.create_task(
            self._debounced_refresh_nomination_reactions(channel_id, message_id)
        )

    async def _delete_nomination_for_payload(
        self, payload: discord.RawReactionActionEvent
    ) -> None:
        pending_task = self._reaction_refresh_tasks.pop(payload.message_id, None)
        if pending_task is not None and not pending_task.done():
            pending_task.cancel()
        async with async_session() as session:
            stmt = select(Nomination).where(Nomination.message_id == payload.message_id)
            nomination = (await session.execute(stmt)).scalar_one_or_none()
            if not nomination or payload.user_id != nomination.nominator_discord_id:
                return

            channel = await self._get_nomination_channel(payload.channel_id)
            if channel is not None and hasattr(channel, "fetch_message"):
                with suppress(Exception):
                    message = await channel.fetch_message(payload.message_id)
                    await message.delete()

            book = await session.get(Book, nomination.book_id)
            await session.delete(nomination)
            if book is not None:
                await session.delete(book)
            await session.commit()

    @app_commands.command(
        name="nominate",
        description="Nominate a book by ISBN, title, or title and author",
    )
    @handle_interaction_errors()
    async def nominate(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer(ephemeral=True)
        query = query.strip()
        if not query:
            raise UserFacingError("Please provide a book title, author, or ISBN.")

        full_title = ""

        async with async_session() as session:
            try:
                lookup = await self.lookup_book(query)
            except BookLookupError:
                logger.exception("Failed to look up book details for query")
                await interaction.followup.send(
                    LOOKUP_ERROR_MESSAGE,
                    ephemeral=True,
                )
                return

            book = await self._find_duplicate_book(session, lookup)

            if book:
                await interaction.followup.send(
                    f"*{book.title}* has previously been nominated.",
                    ephemeral=True,
                )
                return
            else:
                full_title = lookup.full_title
                book = Book(
                    title=full_title,
                    description=lookup.description or "",
                    summary=lookup.summary,
                    isbn=lookup.preferred_isbn,
                    isbn_10=lookup.isbn_10,
                    isbn_13=lookup.isbn_13,
                    authors=lookup.authors,
                    primary_author=lookup.authors[0],
                    length=lookup.page_count,
                )
                session.add(book)
                await session.flush()
                logger.info("Inserted new book {}", book.title)

            # TODO: Add new nomination only if it needs to be re-nominated, else return a message to the user
            nomination = Nomination(
                book_id=book.id,
                nominator_discord_id=interaction.user.id,
                message_id=0,
                reactions=0,
                created_at=utcnow(),
            )
            session.add(nomination)
            await session.flush()
            summary_text = book.summary or "No summary available."
            summary_text += f"\n\nNominated by {interaction.user.mention}."
            if book.length:
                summary_text += f" {book.length} pages."
            embed = discord.Embed(title=book.title, description=summary_text)
            channel = None
            client = getattr(interaction, "client", None)
            if client and hasattr(client, "get_channel"):
                channel = client.get_channel(settings.nom_channel_id)
            if channel is None and client and hasattr(client, "fetch_channel"):
                channel = await client.fetch_channel(settings.nom_channel_id)
            if channel is None:
                await session.rollback()
                raise UserFacingError(
                    "Unable to locate the nominations channel. Please contact an admin."
                )
            try:
                message = await channel.send(embed=embed)
            except Exception:
                await session.rollback()
                logger.exception(
                    "Failed to send nomination message to channel {}",
                    settings.nom_channel_id,
                )
                raise UserFacingError(
                    "Failed to post nomination. Please try again later."
                )
            await message.add_reaction(NOMINATION_CANCEL_EMOJI)
            nomination.message_id = message.id
            session.add(nomination)
            await session.commit()

        await interaction.followup.send(
            f"Nominated *{full_title or book.title}*", ephemeral=True
        )
        if interaction.channel.id != settings.nom_channel_id:
            await interaction.channel.send(
                f"{interaction.user.mention} nominated *{full_title or book.title}*"
            )

    @staticmethod
    def _normalize_match_text(value: str | None) -> str:
        return " ".join(str(value or "").casefold().split())

    @staticmethod
    def _normalize_lookup_isbn(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = ISBN_RE.sub("", str(value)).upper()
        return normalized or None

    async def _find_duplicate_book(
        self, session: Any, lookup: BookLookupResult
    ) -> Book | None:
        isbns = [
            isbn
            for isbn in (
                self._normalize_lookup_isbn(lookup.isbn_10),
                self._normalize_lookup_isbn(lookup.isbn_13),
            )
            if isbn
        ]
        if isbns:
            stmt = select(Book).where(
                or_(
                    Book.isbn.in_(isbns),
                    Book.isbn_10.in_(isbns),
                    Book.isbn_13.in_(isbns),
                )
            )
            duplicate = (await session.execute(stmt)).scalar_one_or_none()
            if duplicate is not None:
                return duplicate

        primary_author = lookup.authors[0]
        stmt = select(Book).where(
            func.lower(Book.primary_author) == primary_author.casefold()
        )
        result = await session.execute(stmt)
        title = self._normalize_match_text(lookup.full_title)
        author = self._normalize_match_text(primary_author)
        for book in result.scalars():
            if (
                self._normalize_match_text(book.title) == title
                and self._normalize_match_text(book.primary_author) == author
            ):
                return book
        return None

    async def lookup_book(self, query: str) -> BookLookupResult:
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        try:
            response = await client.responses.parse(
                model=settings.openai_book_lookup_model,
                instructions=(
                    "You resolve book nominations for a Discord book club. "
                    "Use web search to identify the best matching real book for "
                    "the user's query, which may be an ISBN, title, or title and "
                    "author. For ambiguous title-only queries, pick the most "
                    "canonical or well-known match. Prefer publisher, library, "
                    "or bookseller metadata. Return concise, spoiler-free plain "
                    "metadata only."
                ),
                input=(
                    "Find the best matching book for this nomination query and "
                    f"return structured metadata: {query}"
                ),
                text_format=BookLookupResult,
                tools=[{"type": "web_search", "search_context_size": "medium"}],
                max_output_tokens=1200,
            )
        except (OpenAIError, ValidationError, ValueError) as exc:
            raise BookLookupError from exc

        lookup = response.output_parsed
        if not isinstance(lookup, BookLookupResult):
            raise BookLookupError("OpenAI did not return book metadata")
        return lookup

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.channel_id != settings.nom_channel_id:
            return
        if payload.user_id == getattr(getattr(self.bot, "user", None), "id", None):
            return
        if str(payload.emoji) == NOMINATION_CANCEL_EMOJI:
            await self._delete_nomination_for_payload(payload)
            return
        self._schedule_nomination_reaction_refresh(
            payload.channel_id, payload.message_id
        )

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if payload.channel_id != settings.nom_channel_id:
            return
        if payload.user_id == getattr(getattr(self.bot, "user", None), "id", None):
            return
        if str(payload.emoji) == NOMINATION_CANCEL_EMOJI:
            return
        self._schedule_nomination_reaction_refresh(
            payload.channel_id, payload.message_id
        )
