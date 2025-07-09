from datetime import timedelta, datetime

import discord
from discord import app_commands, Permissions
from discord.ext import commands
from sqlalchemy import select, func, literal_column, true
from sqlalchemy.sql import lateral

from bot.config import get_settings
from bot.db import async_session, Nomination, Election, Vote, Book
from bot.election import close_and_tally
from bot.utils import utcnow, get_open_election

settings = get_settings()


async def get_top_noms(session, limit: int = 0) -> list[tuple[int, int, int]]:
    sub_votes = (
        select(Vote.book_id, func.sum(Vote.weight).label("vote_sum"))
        .group_by(Vote.book_id)
        .subquery()
    )

    elem_col = func.json_array_elements_text(
        Nomination.reacted_users
    ).label("elem")
    elem_sel = select(elem_col)
    unnest_lateral = lateral(elem_sel).alias("n_un")
    nominations = (
        select(Nomination.book_id, func.count(func.distinct(unnest_lateral.c.elem)).label("reacts"))
        .select_from(Nomination)
        .join(unnest_lateral, true())  # cross join
        .group_by(Nomination.book_id)
        .subquery()
    )

    scored = (
        select(
            Book.id,
            func.coalesce(nominations.c.reacts, 0).label("reacts"),
            func.coalesce(sub_votes.c.vote_sum, 0).label("vote_sum"),
            (func.coalesce(nominations.c.reacts, 0) + func.coalesce(sub_votes.c.vote_sum, 0)).label("score"),
        )
        .join(nominations, nominations.c.book_id == Book.id, isouter=True)
        .join(sub_votes, sub_votes.c.book_id == Book.id, isouter=True)
        .where(
            Book.id.not_in(
                select(Election.winner).where(Election.winner.is_not(None))
            )
        )
        .order_by(literal_column("score").desc(), Book.created_at)
    )
    if limit > 0:
        scored = scored.limit(limit)
    return (await session.execute(scored)).all()


class VotingSession(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="open_voting",
        description="Open an election for book club",
    )
    @app_commands.default_permissions(Permissions(manage_roles=True))
    async def open_voting(self, interaction: discord.Interaction, hours: int = 72):
        now = utcnow()
        async with async_session() as session:
            if await get_open_election(session):
                await interaction.response.send_message("An election is already open.", ephemeral=True)
                return

            ballot = await get_top_noms(session, limit=settings.ballot_size)
            if not ballot:
                await interaction.response.send_message("No nominations available for voting.", ephemeral=True)
                return
            closes_at = now + timedelta(hours=hours)
            election = Election(
                opener_discord_id=interaction.user.id,
                opened_at=now,
                closes_at=closes_at,
                ballot=ballot,
            )
            session.add(election)
            await session.commit()
        await self._election_embed(interaction, ballot, closes_at)

    async def _election_embed(self, interaction: discord.Interaction, ballot: list[int], closes_at: datetime):
        closes_at = int(closes_at.timestamp())
        embed = discord.Embed(
            title="Book Club Election",
            description=f"Vote with `/vote`! "
                        f"Election closes <t:{closes_at}:R> on <t:{closes_at}:F>.",
        )
        async with async_session() as session:
            for idx, bid in enumerate(ballot, start=1):
                book = await session.get(Book, bid)
                summary = book.summary or "No summary available."
                if len(summary) > 1024:
                    summary = summary[:1021] + "..."
                embed.add_field(name=f"{idx}. {book.title}", value=summary, inline=False)
        await interaction.client.get_channel(settings.bookclub_channel_id).send(embed=embed)
        await interaction.response.send_message("Election opened.", ephemeral=True)

    @app_commands.command(
        name="close_voting",
        description="Close the current election and announce results",
    )
    @app_commands.default_permissions(Permissions(manage_roles=True))
    async def close_voting(self, interaction: discord.Interaction):
        async with async_session() as session:
            election = await get_open_election(session)
            if not election:
                await interaction.response.send_message("No open election found.", ephemeral=True)
                return
            winner = await close_and_tally(self.bot, session, election, closed_by=interaction.user.id)
        if winner:
            await interaction.response.send_message("Election closed and results announced.", ephemeral=True)
        else:
            await interaction.response.send_message("No votes were cast.")

    @app_commands.command(
        name="ballot_preview",
        description="Preview the current ballot for the next election",
    )
    async def ballot_preview(self, interaction: discord.Interaction, limit: int = settings.ballot_size):
        async with async_session() as session:
            ballot = await get_top_noms(session, limit=limit)
            if not ballot:
                await interaction.response.send_message("No nominations available for voting.", ephemeral=True)
                return
            embed = discord.Embed(title="Upcoming Ballot Preview")
            for idx, (bid, reacts, votes, score) in enumerate(ballot, start=1):
                book = await session.get(Book, bid)
                embed.add_field(
                    name=f"{idx}. {book.title}",
                    value=f"Score: {score:.1f}\n"
                          f"Previous votes: {votes:.1f}\n"
                          f"Reactions: {reacts}",
                    inline=False,
                )
        await interaction.response.send_message(embed=embed, ephemeral=True)
