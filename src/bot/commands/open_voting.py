import discord
from discord import app_commands
from discord.app_commands import Command
from sqlalchemy import select, func
from loguru import logger
from bot.db import async_session, Nomination, Election, Vote, Book
from bot.utils import utcnow
from bot.config import get_settings

settings = get_settings()


class OpenVoting(Command):
    def __init__(self):
        super().__init__(
            name="open_voting",
            description="Open an election for book club",
            callback=self.open_voting,
        )

    async def open_voting(self, interaction: discord.Interaction, hours: int = 72):
        now = utcnow()
        async with async_session() as session:
            existing = (await session.execute(select(Election).where(Election.closed_at.is_(None)))).scalar_one_or_none()
            if existing:
                await interaction.response.send_message("An election is already open.", ephemeral=True)
                return

            sub_votes = (
                select(Vote.book_id, func.sum(Vote.weight).label("vote_sum"))
                .group_by(Vote.book_id)
                .subquery()
            )

            nominations = (
                select(Nomination.book_id, func.count(func.distinct(func.unnest(Nomination.reacted_users))).label("reacts"))
                .group_by(Nomination.book_id)
                .subquery()
            )

            scored = (
                select(
                    Book.id,
                    (func.coalesce(nominations.c.reacts, 0) + func.coalesce(sub_votes.c.vote_sum, 0)).label("score"),
                )
                .join(nominations, nominations.c.book_id == Book.id, isouter=True)
                .join(sub_votes, sub_votes.c.book_id == Book.id, isouter=True)
                .order_by(func.desc("score"), Book.created_at)
                .limit(settings.ballot_size)
            )
            ballot_ids = [row.id for row in (await session.execute(scored)).all()]

            election = Election(
                opener_discord_id=interaction.user.id,
                opened_at=now,
                closes_at=now + hours * 3600,
                ballot=ballot_ids,
                message_id=0,
            )
            session.add(election)
            await session.commit()

        embed = discord.Embed(title="Book Club Election", description="Vote with `/vote`!")
        for idx, bid in enumerate(ballot_ids, 1):
            book = await interaction.client.loop.run_in_executor(None, lambda: session.get(Book, bid))
            embed.add_field(name=f"{idx}. {book.title}", value=book.summary_short, inline=False)
        msg = await interaction.client.get_channel(settings.bookclub_channel_id).send(embed=embed)
        async with async_session() as session:
            await session.execute(
                select(Election).where(Election.id == election.id).execution_options(synchronize_session="fetch")
            )
            election.message_id = msg.id
            await session.commit()
        await interaction.response.send_message("Election opened.", ephemeral=True)
