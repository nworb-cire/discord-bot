import logging
import re

import discord
from discord.ext import commands
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.future import select

from bot.config import get_settings
from bot.db import async_session, Book, Election, Vote
from bot.utils import get_open_election, handle_interaction_errors, short_book_title

log = logging.getLogger(__name__)
settings = get_settings()


class BallotModal(discord.ui.Modal, title="Vote"):
    def __init__(self, noms: list[Book], is_bookclub: bool = False):
        super().__init__()
        self.is_bookclub = is_bookclub
        self.title = f"Points to distribute: {settings.weight_inner if is_bookclub else settings.weight_outer}"
        self.noms = noms
        for nom in noms:
            self.add_item(
                discord.ui.TextInput(
                    label=short_book_title(nom.title)[:45],
                    required=False,
                    placeholder="0-10",
                    default="0",
                )
            )

    async def record_votes(self, user_id, entries):
        max_score = settings.weight_inner if self.is_bookclub else settings.weight_outer
        async with async_session() as session:
            election = await get_open_election(session)
            if not election:
                raise Exception("Voting is not currently open.")
            if sum(v**2 for v in entries.values()) > max_score:
                raise Exception(
                    f"Total score exceeds maximum allowed ({max_score}). "
                    f"Quadratic scoring is used, so scores are squared before summing. "
                    f"i.e., if you cast 3, 3, and 2, the total is 3²+3²+2²=22."
                )
            for book_id, score in entries.items():
                stmt = (
                    insert(Vote)
                    .values(
                        election_id=election.id,
                        voter_discord_id=user_id,
                        book_id=book_id,
                        weight=score,
                    )
                    .on_conflict_do_update(
                        index_elements=[
                            Vote.election_id,
                            Vote.voter_discord_id,
                            Vote.book_id,
                        ],
                        set_={"weight": score},
                    )
                )
                await session.execute(stmt)

            await session.commit()

    async def on_submit(self, inter: discord.Interaction):
        entries = {}
        for comp, nom in zip(self.children, self.noms):
            txt = comp.value.strip() or "0"
            if not re.fullmatch(r"-?\d+(\.\d+)?", txt):
                log.info(
                    "vote_failed",
                    extra={"user": str(inter.user), "reason": "non-numeric input"},
                )
                await inter.response.send_message("Numbers only.", ephemeral=True)
                return
            try:
                entries[nom.id] = float(txt)
            except ValueError:
                log.info(
                    "vote_failed",
                    extra={"user": str(inter.user), "reason": "invalid float"},
                )
                await inter.response.send_message(
                    "Invalid number format.", ephemeral=True
                )
                return
        try:
            await self.record_votes(inter.user.id, entries)
        except Exception as e:
            log.info("vote_failed", extra={"user": str(inter.user), "error": str(e)})
            await inter.response.send_message(str(e), ephemeral=True)
            return
        log.info("vote_success", extra={"user": str(inter.user), "votes": entries})
        await inter.response.send_message("Votes recorded.", ephemeral=True)


class Ballot(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @discord.app_commands.command(
        name="vote", description="Vote for your favorite nominations."
    )
    @handle_interaction_errors()
    async def vote(self, interaction: discord.Interaction):
        async with async_session() as session:
            result = await session.execute(
                select(Election).where(Election.closed_at.is_(None))
            )
            election = result.scalar_one_or_none()
            if not election:
                await interaction.response.send_message(
                    "Voting not open.", ephemeral=True
                )
                return
            books_result = await session.execute(
                select(Book).where(Book.id.in_(election.ballot))
            )
            books = books_result.scalars().all()
        if len(books) == 0:
            await interaction.response.send_message(
                "No nominations available for voting.", ephemeral=True
            )
            return
        # sort books to be in same order as ballot
        books.sort(key=lambda b: election.ballot.index(b.id))
        user_roles = [r.id for r in interaction.user.roles]
        is_bookclub = settings.role_highweight_id in user_roles
        await interaction.response.send_modal(
            BallotModal(books, is_bookclub=is_bookclub)
        )
