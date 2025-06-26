import discord
from discord.app_commands import Command
from loguru import logger
from sqlalchemy import select
from bot.db import async_session, Book, Nomination
from bot.scraper import extract_asin, scrape_amazon_product
from bot.openai_summarize import summarize
from bot.utils import utcnow
from bot.config import get_settings

settings = get_settings()


class Nominate(Command):
    def __init__(self):
        super().__init__(
            name="nominate",
            description="Nominate a book (Amazon link)",
            callback=self.nominate,
        )

    async def nominate(self, interaction: discord.Interaction, link: str):
        asin = extract_asin(link)
        if not asin:
            await interaction.response.send_message("Invalid Amazon link.", ephemeral=True)
            return

        async with async_session() as session:
            book_stmt = select(Book).where(Book.asin == asin)
            book = (await session.execute(book_stmt)).scalar_one_or_none()

            if not book:
                meta = await scrape_amazon_product(asin)
                # short, long = await summarize(meta["title"])
                short, long = "", ""

                book = Book(
                    asin=asin,
                    author=meta["author"],
                    title=meta["title"],
                    price_paperback=meta["price_paperback"] or 0.0,
                    price_kindle=meta["price_kindle"],
                    price_audible=meta["price_audible"],
                    summary_short=short,
                    summary_long=long,
                )
                session.add(book)
                await session.commit()
                logger.info("Inserted new book {}", asin)

            nomination = Nomination(
                book_id=book.id,
                nominator_discord_id=interaction.user.id,
                message_id=0,
                reacted_users=[],
                created_at=utcnow(),
            )
            session.add(nomination)
            await session.commit()

        embed = discord.Embed(title=book.title, description=book.summary_short)
        embed.add_field(name="Author", value=book.author, inline=True)
        embed.add_field(name="Paperback", value=f"${book.price_paperback}", inline=True)
        if book.price_kindle:
            embed.add_field(name="Kindle", value=f"${book.price_kindle}", inline=True)
        await interaction.client.get_channel(settings.nom_channel_id).send(embed=embed)
        await interaction.response.send_message("Nomination posted!", ephemeral=True)
