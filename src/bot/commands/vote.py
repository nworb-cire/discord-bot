import re
import logging

import discord
from discord.ext import commands
from sqlalchemy.future import select
from sqlalchemy import func

from bot.config import get_settings
from bot.db import async_session, Book, Election, Nomination, Vote

log = logging.getLogger(__name__)
settings = get_settings()

class BallotModal(discord.ui.Modal, title="Vote"):
    def __init__(self, noms: list[Book]):
        super().__init__()
        self.noms = noms
        for nom in noms:
            self.add_item(
                discord.ui.TextInput(
                    label=nom.title[:45],
                    required=False,
                    placeholder="0-10",
                    default="0",
                )
            )

    async def record_votes(self, user_id, entries, is_bookclub):
        max_score = settings.weight_inner if is_bookclub else settings.weight_outer
        async with async_session() as session:
            result = await session.execute(select(Election).where(Election.closed_at.is_(None)))
            election = result.scalar_one_or_none()
            if not election:
                raise Exception("Voting is not currently open.")
            if sum(v**2 for v in entries.values()) > max_score:
                raise Exception(f"Total score exceeds maximum allowed ({max_score})."
                                f"Quadratic scoring is used, so scores are squared before summing.")
            for book_id, score in entries.items():
                vote = Vote(
                    election_id=election.id,
                    voter_discord_id=user_id,
                    book_id=book_id,
                    weight=score,
                )
               # fixme: users cannot change their votes due to uniqueness constraints. maybe use upsert?
                session.add(vote)
            await session.commit()

    async def on_submit(self, inter: discord.Interaction):
        entries = {}
        for comp, nom in zip(self.children, self.noms):
            txt = comp.value.strip() or "0"
            if not re.fullmatch(r"-?\d+(\.\d+)?", txt):
                log.info("vote_failed", extra={"user": str(inter.user), "reason": "non-numeric input"})
                await inter.response.send_message("Numbers only.", ephemeral=True)
                return
            entries[nom.id] = int(txt)
        roles = [r.name.lower() for r in inter.user.roles]
        try:
            await self.record_votes(inter.user.id, entries, settings.role_highweight_id in roles)
        except Exception as e:
            log.info("vote_failed", extra={"user": str(inter.user), "error": str(e)})
            await inter.response.send_message(str(e), ephemeral=True)
            return
        log.info("vote_success", extra={"user": str(inter.user), "votes": entries})
        await inter.response.send_message("Votes recorded.", ephemeral=True)


class Ballot(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @discord.app_commands.command(name="vote", description="Vote for your favorite nominations.")
    async def vote(self, interaction: discord.Interaction):
        async with async_session() as session:
            result = await session.execute(select(Election).where(Election.closed_at.is_(None)))
            election = result.scalar_one_or_none()
            if not election:
                await interaction.response.send_message("Voting not open.", ephemeral=True)
                return
            books_result = await session.execute(
                select(Book).join(Nomination, Book.id == Nomination.book_id)
                .where(Nomination.id.in_(election.ballot))
            )
            books = books_result.scalars().all()
            result = await session.execute(
                select(Vote.book_id, func.sum(Vote.weight).label("total_votes"))
                .where(Vote.election_id == election.id)
                .group_by(Vote.book_id)
                .order_by(func.sum(Vote.weight).desc())  # TODO: break ties consistently
            )
            vote_totals = result.all()
        await interaction.response.send_modal(BallotModal(books))
