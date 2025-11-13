import asyncio
from datetime import timedelta, datetime
from dataclasses import dataclass
from typing import Optional

import discord
from discord import app_commands, Permissions
from discord.ext import commands
from loguru import logger
from sqlalchemy import select, func, Integer, cast

from bot.config import get_settings
from bot.db import async_session, Nomination, Election, Vote, Book
from bot.reactions import update_election_vote_reaction
from bot.election import close_and_tally, get_election_vote_totals
from bot.utils import (
    NOMINATION_CANCEL_EMOJI,
    format_vote_count,
    get_open_election,
    handle_interaction_errors,
    nomination_message_url,
    utcnow,
    short_book_title,
)

settings = get_settings()


@dataclass(slots=True)
class BallotNominee:
    book_id: int
    reactions: int
    vote_sum: float
    score: float
    prior_appearances: int


@dataclass(slots=True)
class BallotEntryDetails:
    book: Book
    nomination: Optional[Nomination]
    jump_url: Optional[str]


@dataclass(slots=True)
class VoteSummary:
    total_votes: float
    text: str


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
    ) -> list[BallotEntryDetails]:
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

        entries: list[BallotEntryDetails] = []
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
            entries.append(
                BallotEntryDetails(book=book, nomination=nomination, jump_url=jump_url)
            )
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

    async def get_top_noms(self, session, limit: int = 0) -> list[BallotNominee]:
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
        ballot_entries = (
            select(
                cast(func.json_array_elements_text(Election.ballot), Integer).label(
                    "book_id"
                )
            )
            .select_from(Election)
            .where(Election.winner.is_not(None))
            .cte("ballot_entries")
        )
        appearance_totals = (
            select(
                ballot_entries.c.book_id,
                func.count().label("appearance_count"),
            )
            .group_by(ballot_entries.c.book_id)
            .cte("appearance_totals")
        )
        appearance_count_expr = func.coalesce(appearance_totals.c.appearance_count, 0)
        max_appearances = settings.max_election_appearances
        stmt = (
            select(
                Book.id.label("book_id"),
                Book.created_at.label("created_at"),
                func.coalesce(nominations_table.c.reactions, 0).label("reactions"),
                func.coalesce(sub_votes.c.vote_sum, 0).label("vote_sum"),
                (
                    func.coalesce(nominations_table.c.reactions, 0)
                    + func.coalesce(sub_votes.c.vote_sum, 0)
                ).label("score"),
                appearance_count_expr.label("appearance_count"),
            )
            .select_from(Book)
            .outerjoin(nominations_table, nominations_table.c.book_id == Book.id)
            .outerjoin(sub_votes, sub_votes.c.book_id == Book.id)
            .outerjoin(appearance_totals, appearance_totals.c.book_id == Book.id)
            .where(~Book.id.in_(winner_subq))
        )
        if not settings.is_staging:
            stmt = stmt.where(func.coalesce(nominations_table.c.reactions, 0) > 0)
        stmt = stmt.where(appearance_count_expr < max_appearances)
        result = await session.execute(stmt)
        rows = result.all()
        if not rows:
            return []
        candidates: list[dict[str, object]] = []
        for row in rows:
            book_id = int(row.book_id)
            prior_appearances = int(getattr(row, "appearance_count", 0) or 0)
            if prior_appearances >= max_appearances:
                continue
            vote_sum = float(row.vote_sum) if row.vote_sum is not None else 0.0
            score = float(row.score) if row.score is not None else 0.0
            created_at = getattr(row, "created_at", None)
            created_order = (
                created_at.timestamp()
                if created_at is not None and hasattr(created_at, "timestamp")
                else float("-inf")
            )
            candidates.append(
                {
                    "nominee": BallotNominee(
                        book_id=book_id,
                        reactions=int(row.reactions),
                        vote_sum=vote_sum,
                        score=score,
                        prior_appearances=prior_appearances,
                    ),
                    "has_prior": prior_appearances > 0,
                    "score": score,
                    "created_order": created_order,
                }
            )
        ordered_entries = [
            item["nominee"]
            for item in sorted(
                candidates,
                key=lambda item: (
                    item["has_prior"],
                    -item["score"],
                    item["created_order"],
                ),
            )
        ]
        if limit > 0:
            ordered_entries = ordered_entries[:limit]
        return ordered_entries

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
            ballot_ids = [nominee.book_id for nominee in ballot]
            if not ballot:
                await interaction.followup.send(
                    "No nominations available for voting.", ephemeral=True
                )
                return
            max_appearances = settings.max_election_appearances
            star_threshold = max_appearances - 1 if max_appearances > 0 else None
            last_appearance_ids = {
                nominee.book_id
                for nominee in ballot
                if star_threshold is not None
                and nominee.prior_appearances == star_threshold
            }
            closes_at = now + timedelta(hours=hours)
            election = Election(
                opener_discord_id=interaction.user.id,
                opened_at=now,
                closes_at=closes_at,
                ballot=ballot_ids,
            )
            session.add(election)
            await session.commit()
            await session.refresh(election)
        await self._election_embed(
            interaction,
            election.id,
            ballot_ids,
            closes_at,
            last_appearance_ids,
        )

    async def _election_embed(
        self,
        interaction: discord.Interaction,
        election_id: int,
        ballot: list[int],
        closes_at: datetime,
        last_appearance_ids: Optional[set[int]] = None,
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
            for idx, entry in enumerate(entries, start=1):
                book = entry.book
                title = short_book_title(book.title)
                if last_appearance_ids and book.id in last_appearance_ids:
                    title += " *"
                field_name = (
                    f"{idx}. {title} {entry.jump_url}"
                    if entry.jump_url is not None
                    else f"{idx}. {title}"
                )
                summary = book.summary or "No summary available."
                if len(summary) > 1024:
                    summary = summary[:1021] + "..."
                embed.add_field(name=field_name, value=summary, inline=False)
        channel = interaction.client.get_channel(settings.bookclub_channel_id)
        if channel is None:
            channel = await interaction.client.fetch_channel(
                settings.bookclub_channel_id
            )
        message = await channel.send(embed=embed)
        async with async_session() as session:
            election = await session.get(Election, election_id)
            if election:
                election.ballot_message_id = message.id
                session.add(election)
                await session.commit()
        try:
            await update_election_vote_reaction(interaction.client, election_id)
        except Exception:
            logger.exception(
                "Failed to set initial vote reaction for election %s",
                election_id,
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
        name="result_preview",
        description="Preview the current vote totals for the open election",
    )
    @app_commands.default_permissions(Permissions(manage_roles=True))
    @handle_interaction_errors()
    async def result_preview(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        async with async_session() as session:
            election = await get_open_election(session)
            if not election:
                await interaction.followup.send(
                    "No open election found.", ephemeral=True
                )
                return
            ballot_ids = list(election.ballot or [])
            if not ballot_ids:
                await interaction.followup.send(
                    "The open election has no ballot.", ephemeral=True
                )
                return
            totals_rows = await get_election_vote_totals(session, election.id)
            totals = {book.id: votes for book, votes in totals_rows}
            books_result = await session.execute(
                select(Book).where(Book.id.in_(ballot_ids))
            )
            books = {book.id: book for book in books_result.scalars().all()}

        summaries: list[VoteSummary] = []
        for book_id in ballot_ids:
            book = books.get(book_id)
            if not book:
                continue
            total = totals.get(book_id, 0.0)
            summaries.append(
                VoteSummary(
                    total_votes=total,
                    text=f"{short_book_title(book.title)}: {format_vote_count(total)}",
                )
            )

        if not summaries:
            await interaction.followup.send(
                "No eligible books found for the open election.", ephemeral=True
            )
            return

        summaries.sort(key=lambda item: item.total_votes, reverse=True)
        content = "\n".join(item.text for item in summaries)
        await interaction.followup.send(content, ephemeral=True)

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
            book_ids = [nominee.book_id for nominee in ballot]
            guild_id = self._resolve_guild_id(interaction)
            entries = await self._get_ballot_entries(session, book_ids, guild_id)
            entry_lookup = {entry.book.id: entry for entry in entries}
            max_appearances = settings.max_election_appearances
            star_threshold = max_appearances - 1 if max_appearances > 0 else None

            def _format_score(value: float) -> str:
                text = f"{value:.1f}"
                trimmed = text.rstrip("0").rstrip(".")
                return trimmed or "0"

            for idx, nominee in enumerate(ballot, start=1):
                entry = entry_lookup.get(nominee.book_id)
                if entry is None:
                    continue
                book = entry.book
                jump_url = entry.jump_url
                title = short_book_title(book.title)
                if (
                    star_threshold is not None
                    and nominee.prior_appearances == star_threshold
                ):
                    title += " *"
                field_name = (
                    f"{idx}. {title} {jump_url}"
                    if jump_url is not None
                    else f"{idx}. {title}"
                )
                embed.add_field(
                    name=field_name,
                    value=(
                        f"Score: {_format_score(nominee.score)} "
                        f"({format_vote_count(nominee.vote_sum)} votes + {nominee.reactions} seconds)"
                    ),
                    inline=False,
                )
        await interaction.followup.send(embed=embed, ephemeral=True)
