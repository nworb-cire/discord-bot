import discord
from discord import app_commands, Permissions
from discord.ext import commands
from sqlalchemy import select, func, literal_column, true, update
from sqlalchemy.sql import lateral
from bot.db import async_session, Nomination, Election, Vote, Book
from bot.utils import utcnow
from datetime import timedelta, datetime
from bot.config import get_settings

settings = get_settings()


class VotingSession(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _get_ballot(self, session):
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
                (func.coalesce(nominations.c.reacts, 0) + func.coalesce(sub_votes.c.vote_sum, 0)).label("score"),
            )
            .join(nominations, nominations.c.book_id == Book.id, isouter=True)
            .join(sub_votes, sub_votes.c.book_id == Book.id, isouter=True)
            .order_by(literal_column("score").desc(), Book.created_at)
            .limit(settings.ballot_size)
        )
        ballot_ids = [row.id for row in (await session.execute(scored)).all()]

        # filter out previous winners, todo do this in the query
        previous_winners = (
            await session.execute(
                select(Election.winner).where(Election.winner.is_not(None))
            )
        ).scalars().all()
        ballot_ids = [bid for bid in ballot_ids if bid not in previous_winners]
        return ballot_ids

    async def _get_open_election(self, session):
        return (await session.execute(
            select(Election)
            .where(Election.closed_at.is_(None))
            .order_by(Election.opened_at.desc())
        )).scalar_one_or_none()

    @app_commands.command(
        name="open_voting",
        description="Open an election for book club",
    )
    @app_commands.default_permissions(Permissions(manage_roles=True))
    async def open_voting(self, interaction: discord.Interaction, hours: int = 72):
        now = utcnow()
        async with async_session() as session:
            if await self._get_open_election(session):
                await interaction.response.send_message("An election is already open.", ephemeral=True)
                return

            ballot = await self._get_ballot(session)
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
        await self._election_embed(interaction, election, ballot, closes_at)

    async def _election_embed(self, interaction: discord.Interaction, election: Election, ballot: list[int], closes_at: datetime):
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
        await interaction.response.send_message("Election opened.", ephemeral=True)

    @app_commands.command(
        name="close_voting",
        description="Close the current election and announce results",
    )
    @app_commands.default_permissions(Permissions(manage_roles=True))
    async def close_voting(self, interaction: discord.Interaction):
        async with async_session() as session:
            election = await self._get_open_election(session)
            if not election:
                await interaction.response.send_message("No open election found.", ephemeral=True)
                return

            # close *all* elections, not just the "current" one, just in case
            await session.execute(
                update(Election)
                .where(Election.closed_at.is_(None))
                .values(
                    closed_by=interaction.user.id,
                    closed_at=utcnow(),
                )
            )
            await session.commit()

            votes = (
                await session.execute(
                    select(Book, func.sum(Vote.weight).label("total_votes"))
                    .join(Book, Book.id == Vote.book_id)
                    .where(Vote.election_id == election.id)
                    .group_by(Book)
                    .order_by(func.sum(Vote.weight).desc())  # TODO: break ties consistently
                )
            )
            all_votes = votes.all()
            winner, _ = all_votes[0] if all_votes else (None, 0)
            if not winner:
                await interaction.response.send_message("No votes were cast.")
                return
            election.winner = winner.id

            await session.commit()

        embed = discord.Embed(title="Election Results", description="Voting has ended.")
        embed.add_field(name="Winner", value=winner.title, inline=False)
        for idx, (book, votes) in enumerate(all_votes, start=1):
            embed.add_field(name=f"{idx}. {book.title}", value=f"Votes: {votes}", inline=False)
        await interaction.client.get_channel(settings.bookclub_channel_id).send(embed=embed)
        await interaction.response.send_message("Election closed and results announced.", ephemeral=True)
