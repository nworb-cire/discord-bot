import re
from typing import Any

import discord
import httpx
import openai
from discord.app_commands import Command
from loguru import logger
from sqlalchemy import select

from bot.config import get_settings
from bot.db import async_session, Book, Nomination
from bot.utils import utcnow

settings = get_settings()
ASIN_RE = re.compile(r"/([A-Z0-9]{10})(?:[/?]|$)")


class Nominate(Command):
    def __init__(self):
        super().__init__(
            name="nominate",
            description="Nominate a book (ISBN)",
            callback=self.nominate,
        )
        self.openai_client = openai.AsyncOpenAI(api_key=settings.openai_key)

    async def nominate(self, interaction: discord.Interaction, isbn: str):
        isbn = isbn.strip()

        async with async_session() as session:
            book_stmt = select(Book).where(Book.isbn10 == isbn)
            book = (await session.execute(book_stmt)).scalar_one_or_none()

            if not book:
                meta = await self.open_library_search(isbn)
                if not meta:
                    await interaction.response.send_message("Failed to find book in OpenLibrary.org.", ephemeral=True)
                    return

                title = meta.get("title", "Unknown Title")
                subtitle = meta.get("subtitle", "")
                full_title = f"{title}: {subtitle}" if subtitle else title
                book = Book(
                    title=full_title,
                    description=meta.get("description", {}).get("value", ""),
                    isbn10=isbn,
                    length=meta.get("number_of_pages", None),
                )
                session.add(book)
                await session.commit()
                logger.info("Inserted new book {}", book.isbn10)

            # TODO: Add new nomination only if it needs to be re-nominated, else return a message to the user
            nomination = Nomination(
                book_id=book.id,
                nominator_discord_id=interaction.user.id,
                message_id=0,
                reacted_users=[],
                created_at=utcnow(),
            )
            session.add(nomination)
            await session.commit()

        summary_short = await self.openai_summarize(book.title, book.description or "")
        embed = discord.Embed(title=book.title, description=summary_short)
        await interaction.client.get_channel(settings.nom_channel_id).send(embed=embed)
        await interaction.response.send_message("Nomination posted!", ephemeral=True)

    async def open_library_search(self, isbn: str) -> dict[str, Any]:
        with httpx.AsyncClient() as client:
            r = await client.get(f"https://openlibrary.org/books/{isbn}.json")
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
        return response.choices[0].message.content.strip() if response.choices else "No summary available."
