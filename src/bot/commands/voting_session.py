import asyncio
from datetime import timedelta, datetime
from typing import Optional

import discord
from discord import app_commands, Permissions
from discord.ext import commands
from loguru import logger
from sqlalchemy import select, func, literal_column

from bot.config import get_settings
from bot.db import async_session, Nomination, Election, Vote, Book
from bot.election import close_and_tally
from bot.utils import (
    NOMINATION_CANCEL_EMOJI,
    get_open_election,
    handle_interaction_errors,
    nomination_message_url,
    utcnow,
    short_book_title,
)

settings = get_settings()


class VotingSession(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @staticmethod
    def _resolve_guild_id(interaction: discord.Interaction) -> Optional[int]:
        guild_id = getattr(interaction, "guild_id", None)
        if guild_id is None:
            guild = getattr(interaction, "guild", None)
            guild_id = getattr(guild, "id", None) if guild is not None else None
        return guild_id

    async def _get_ballot_entries(
        self,
        session,
        ballot_ids: list[int],
        guild_id: Optional[int],
    ) -> list[tuple[Book, Optional[Nomination], Optional[str]]]:
        if not ballot_ids:
            return []

        nominations_result = await session.execute(
            select(Nomination).where(Nomination.book_id.in_(ballot_ids))
        )
        nominations_by_book = {
            nomination.book_id: nomination
            for nomination in nominations_result.scalars()
        }
        books_result = await session.execute(
            select(Book).where(Book.id.in_(ballot_ids))
        )
        books_by_id = {book.id: book for book in books_result.scalars()}

        entries: list[tuple[Book, Optional[Nomination], Optional[str]]] = []
        for bid in ballot_ids:
            book = books_by_id.get(bid)
            if not book:
                continue
            nomination = nominations_by_book.get(bid)
            jump_url = (
                nomination_message_url(nomination.message_id, guild_id)
                if nomination
                else None
            )
            entries.append((book, nomination, jump_url))
        return entries

    async def get_reacts_for_nomination(self, nomination: Nomination) -> int:
        """Get the number of unique users who reacted to a nomination."""
        channel = self.bot.get_channel(settings.nom_channel_id)
        if not channel:
            channel = await self.bot.fetch_channel(settings.nom_channel_id)
        try:
            message = await channel.fetch_message(nomination.message_id)
        except discord.NotFound:
            logger.warning(
                "Nomination message {} for book {} no longer exists; defaulting reactions to 0",
                nomination.message_id,
                nomination.book_id,
            )
            return 0

        unique_users = set()
        for reaction in message.reactions:
            emoji = getattr(reaction, "emoji", reaction)
            if str(emoji) == NOMINATION_CANCEL_EMOJI:
                continue
            async for user in reaction.users():
                unique_users.add(user.id)
        return len(unique_users - {nomination.nominator_discord_id})

    async def update_all_nominations(self, session):
        nominations = await session.execute(select(Nomination))
        nominations = nominations.scalars().all()

        async def update_nom(nomination):
            nomination.reactions = await self.get_reacts_for_nomination(nomination)
            session.add(nomination)

        await asyncio.gather(*(update_nom(n) for n in nominations))
        await session.commit()

    async def get_top_noms(
        self, session, limit: int = 0
    ) -> list[tuple[int, int, float, float]]:
        await self.update_all_nominations(session)
        sub_votes = (
            select(Vote.book_id, func.sum(Vote.weight).label("vote_sum"))
            .group_by(Vote.book_id)
            .subquery()
        )
        nominations_table = Nomination.__table__
        winner_subq = (
            select(Election.winner)
            .where(Election.winner.is_not(None))
            .scalar_subquery()
        )
        stmt = (
            select(
                Book.id.label("book_id"),
                func.coalesce(nominations_table.c.reactions, 0).label("reactions"),
                func.coalesce(sub_votes.c.vote_sum, 0).label("vote_sum"),
                (
                    func.coalesce(nominations_table.c.reactions, 0)
                    + func.coalesce(sub_votes.c.vote_sum, 0)
                ).label("score"),
            )
            .select_from(Book)
            .outerjoin(nominations_table, nominations_table.c.book_id == Book.id)
            .outerjoin(sub_votes, sub_votes.c.book_id == Book.id)
            .where(~Book.id.in_(winner_subq))
            .order_by(literal_column("score").desc(), Book.created_at)
        )
        if limit > 0:
            stmt = stmt.limit(limit)
        result = await session.execute(stmt)
        return [
            (
                int(row.book_id),
                int(row.reactions),
                float(row.vote_sum) if row.vote_sum is not None else 0.0,
                float(row.score) if row.score is not None else 0.0,
            )
            for row in result.all()
        ]

    @app_commands.command(
        name="open_voting",
        description="Open an election for book club",
    )
    @app_commands.default_permissions(Permissions(manage_roles=True))
    @handle_interaction_errors()
    @app_commands.describe(
        hours="Number of hours the election should remain open",
        ballot_size="How many nominations to include in the ballot",
    )
    async def open_voting(
        self,
        interaction: discord.Interaction,
        hours: int = 72,
        ballot_size: int = 5,
    ):
        await interaction.response.defer(ephemeral=True)
        now = utcnow()
        async with async_session() as session:
            if await get_open_election(session):
                await interaction.followup.send(
                    "An election is already open.", ephemeral=True
                )
                return

            ballot = await self.get_top_noms(session, limit=ballot_size)
            ballot_ids = [bid for bid, _, _, _ in ballot]
            if not ballot:
                await interaction.followup.send(
                    "No nominations available for voting.", ephemeral=True
                )
                return
            closes_at = now + timedelta(hours=hours)
            election = Election(
                opener_discord_id=interaction.user.id,
                opened_at=now,
                closes_at=closes_at,
                ballot=ballot_ids,
            )
            session.add(election)
            await session.commit()
        await self._election_embed(interaction, ballot_ids, closes_at)

    async def _election_embed(
        self, interaction: discord.Interaction, ballot: list[int], closes_at: datetime
    ):
        closes_at = int(closes_at.timestamp())
        embed = discord.Embed(
            title="Book Club Election",
            description=f"Vote with `/vote`! "
            f"Election closes <t:{closes_at}:R> on <t:{closes_at}:F>.",
        )
        async with async_session() as session:
            guild_id = self._resolve_guild_id(interaction)
            entries = await self._get_ballot_entries(session, ballot, guild_id)
            for idx, (book, _nomination, jump_url) in enumerate(entries, start=1):
                title = short_book_title(book.title)
                field_name = (
                    f"{idx}. {title} {jump_url}"
                    if jump_url is not None
                    else f"{idx}. {title}"
                )
                summary = book.summary or "No summary available."
                if len(summary) > 1024:
                    summary = summary[:1021] + "..."
                embed.add_field(name=field_name, value=summary, inline=False)
        await interaction.client.get_channel(settings.bookclub_channel_id).send(
            embed=embed
        )
        await interaction.followup.send("Election opened.", ephemeral=True)

    @app_commands.command(
        name="close_voting",
        description="Close the current election and announce results",
    )
    @app_commands.default_permissions(Permissions(manage_roles=True))
    @handle_interaction_errors()
    async def close_voting(self, interaction: discord.Interaction):
        async with async_session() as session:
            election = await get_open_election(session)
            if not election:
                await interaction.response.send_message(
                    "No open election found.", ephemeral=True
                )
                return
            winner = await close_and_tally(
                self.bot, session, election, closed_by=interaction.user.id
            )
        if winner:
            await interaction.response.send_message(
                "Election closed and results announced.", ephemeral=True
            )
        else:
            await interaction.response.send_message("No votes were cast.")

    @app_commands.command(
        name="ballot_preview",
        description="Preview the current ballot for the next election",
    )
    @handle_interaction_errors()
    async def ballot_preview(self, interaction: discord.Interaction, limit: int = 5):
        await interaction.response.defer(ephemeral=True)
        async with async_session() as session:
            if await get_open_election(session):
                await interaction.followup.send(
                    "An election is currently open. Cannot preview ballot.",
                    ephemeral=True,
                )
                return
            ballot = await self.get_top_noms(session, limit=limit)
            if not ballot:
                await interaction.followup.send(
                    "No nominations available for voting.", ephemeral=True
                )
                return
            embed = discord.Embed(title="Upcoming Ballot Preview")
            book_ids = [bid for bid, _, _, _ in ballot]
            guild_id = self._resolve_guild_id(interaction)
            entries = await self._get_ballot_entries(session, book_ids, guild_id)
            entry_lookup = {entry[0].id: entry for entry in entries}
            for idx, (bid, reacts, votes, score) in enumerate(ballot, start=1):
                entry = entry_lookup.get(bid)
                if entry is None:
                    continue
                book, _nomination, jump_url = entry
                title = short_book_title(book.title)
                field_name = (
                    f"{idx}. {title} {jump_url}"
                    if jump_url is not None
                    else f"{idx}. {title}"
                )
                embed.add_field(
                    name=field_name,
                    value=f"Score: {score:.1f}\n"
                    f"Previous votes: {votes:.1f}\n"
                    f"Reactions: {reacts}",
                    inline=False,
                )
        await interaction.followup.send(embed=embed, ephemeral=True)
