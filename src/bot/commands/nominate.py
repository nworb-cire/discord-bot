import re
from contextlib import suppress
from typing import Any

import discord
import httpx
import openai
from discord import app_commands
from discord.ext import commands
from loguru import logger
from sqlalchemy import select

from bot.config import get_settings
from bot.db import async_session, Book, Nomination
from bot.utils import NOMINATION_CANCEL_EMOJI, handle_interaction_errors, utcnow

settings = get_settings()
ASIN_RE = re.compile(r"/([A-Z0-9]{10})(?:[/?]|$)")


class Nominate(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.openai_client = openai.AsyncOpenAI(api_key=settings.openai_key)

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
                    await interaction.followup.send("Failed to fetch book metadata from OpenLibrary.org.", ephemeral=True)
                    return
                if not meta:
                    await interaction.followup.send("Failed to find book in OpenLibrary.org.", ephemeral=True)
                    return

                title = meta.get("title", "Unknown Title")
                subtitle = meta.get("subtitle", "")
                full_title = f"{title}: {subtitle}" if subtitle else title
                description = str(meta.get("description", ""))  # Sometimes this comes through as a nested dict
                summary = await self.openai_summarize(full_title, description)
                book = Book(
                    title=full_title,
                    description=description,
                    summary=summary,
                    isbn=isbn,
                    length=meta.get("number_of_pages", None),
                )
                session.add(book)
                await session.commit()
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
            await session.commit()
            summary_text = book.summary or "No summary available."
            if book.summary:
                summary_text += "\n\nNote: AI generated summaries may be inaccurate."
            summary_text += f"\n\nNominated by {interaction.user.mention}."
            if book.length:
                summary_text += f" {book.length} pages."
            embed = discord.Embed(title=book.title, description=summary_text)
            channel = interaction.client.get_channel(settings.nom_channel_id)
            message = await channel.send(embed=embed)
            await message.add_reaction(NOMINATION_CANCEL_EMOJI)
            nomination.message_id = message.id
            session.add(nomination)
            await session.commit()

        await interaction.followup.send(f"Nominated *{full_title or book.title}*", ephemeral=True)
        if interaction.channel.id != settings.nom_channel_id:
            await interaction.channel.send(f"{interaction.user.mention} nominated *{full_title or book.title}*")

    async def open_library_search(self, isbn: str) -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"https://openlibrary.org/isbn/{isbn}.json", follow_redirects=True)
            r.raise_for_status()
        return r.json()

    async def openai_summarize(self, title: str, description: str) -> str:
        response = await self.openai_client.responses.create(
            model="gpt-4o-mini",
            instructions="You are a helpful librarian whose job is to convince smart people why they might "
                         "want to read certain books, by giving accurate and compelling summaries of them. "
                         "You are to provide a three-sentence summary of the book..",
            input=f"Book title: {title}\nDescription: {description}",
        )
        return response.output[0].content[0].text.strip() if response.output else "No summary available."

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.channel_id != settings.nom_channel_id:
            return
        if str(payload.emoji) != NOMINATION_CANCEL_EMOJI:
            return
        if payload.user_id == getattr(getattr(self.bot, "user", None), "id", None):
            return

        async with async_session() as session:
            stmt = select(Nomination).where(Nomination.message_id == payload.message_id)
            nomination = (await session.execute(stmt)).scalar_one_or_none()
            if not nomination or payload.user_id != nomination.nominator_discord_id:
                return

            channel = None
            if hasattr(self.bot, "get_channel"):
                channel = self.bot.get_channel(payload.channel_id)
            if channel is None and hasattr(self.bot, "fetch_channel"):
                channel = await self.bot.fetch_channel(payload.channel_id)

            if channel is not None:
                with suppress(Exception):
                    message = await channel.fetch_message(payload.message_id)
                    await message.delete()

            book = await session.get(Book, nomination.book_id)
            await session.delete(nomination)
            if book is not None:
                await session.delete(book)
            await session.commit()
