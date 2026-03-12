import asyncio
import re
from contextlib import suppress
from typing import Any

import discord
import httpx
from bs4 import BeautifulSoup
from discord import app_commands
from discord.ext import commands
from loguru import logger
from sqlalchemy import select

from bot.config import get_settings
from bot.db import async_session, Book, Nomination
from bot.utils import (
    NOMINATION_CANCEL_EMOJI,
    UserFacingError,
    handle_interaction_errors,
    utcnow,
)

settings = get_settings()
ASIN_RE = re.compile(r"/([A-Z0-9]{10})(?:[/?]|$)")


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
        description="Nominate a book by its ISBN",
    )
    @handle_interaction_errors()
    async def nominate(self, interaction: discord.Interaction, isbn: str):
        await interaction.response.defer(ephemeral=True)
        isbn = re.sub(r"[^\dX]", "", isbn)

        full_title = ""

        async with async_session() as session:
            book_stmt = select(Book).where(Book.isbn == isbn)
            book = (await session.execute(book_stmt)).scalar_one_or_none()

            if book:
                await interaction.followup.send(
                    f"Book with ISBN {isbn} has previously been nominated: *{book.title}*",
                    ephemeral=True,
                )
                return
            else:
                try:
                    meta = await self.open_library_search(isbn)
                except httpx.HTTPStatusError as e:
                    logger.error("Failed to fetch book metadata: {}", e)
                    await interaction.followup.send(
                        "Failed to fetch book metadata from OpenLibrary.org.",
                        ephemeral=True,
                    )
                    return
                if not meta:
                    await interaction.followup.send(
                        "Failed to find book in OpenLibrary.org.", ephemeral=True
                    )
                    return

                title = meta.get("title", "Unknown Title")
                subtitle = meta.get("subtitle", "")
                full_title = f"{title}: {subtitle}" if subtitle else title
                description = self._normalize_description(meta.get("description", ""))
                summary = await self.fetch_summary(isbn, description)
                book = Book(
                    title=full_title,
                    description=description,
                    summary=summary,
                    isbn=isbn,
                    length=meta.get("number_of_pages", None),
                )
                session.add(book)
                await session.flush()
                logger.info(f"Inserted new book {book.isbn}")

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

    async def open_library_search(self, isbn: str) -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"https://openlibrary.org/isbn/{isbn}.json", follow_redirects=True
            )
            r.raise_for_status()
        return r.json()

    @staticmethod
    def _normalize_description(description: Any) -> str:
        if isinstance(description, dict):
            description = description.get("value", "")
        text = BeautifulSoup(str(description or ""), "html.parser").get_text(" ")
        return " ".join(text.split())

    @staticmethod
    def _matches_isbn(item: dict[str, Any], isbn: str) -> bool:
        identifiers = item.get("volumeInfo", {}).get("industryIdentifiers", [])
        normalized_isbn = isbn.upper()
        for identifier in identifiers:
            value = str(identifier.get("identifier", "")).replace("-", "").upper()
            if value == normalized_isbn:
                return True
        return False

    async def google_books_summary(self, isbn: str) -> str:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://www.googleapis.com/books/v1/volumes",
                params={"q": f"isbn:{isbn}"},
            )
            response.raise_for_status()

        data = response.json()
        items = data.get("items", [])
        if not items:
            return ""

        match = next(
            (item for item in items if self._matches_isbn(item, isbn)),
            items[0],
        )
        description = match.get("volumeInfo", {}).get("description", "")
        return self._normalize_description(description)

    async def fetch_summary(self, isbn: str, open_library_description: str) -> str:
        try:
            summary = await self.google_books_summary(isbn)
        except httpx.HTTPError:
            logger.exception(
                "Failed to fetch Google Books summary for ISBN {}",
                isbn,
            )
        else:
            if summary:
                return summary

        if open_library_description:
            return open_library_description

        return "No summary available."

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
