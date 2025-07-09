import discord
from sqlalchemy import update, select, func

from loguru import logger

from bot.config import get_settings
from bot.db import Election, Vote, Book
from bot.utils import utcnow

settings = get_settings()

async def close_and_tally(client, session, election, closed_by=None):
    now = utcnow()
    values = {"closed_at": now}
    if closed_by is not None:
        values["closed_by"] = closed_by
    await session.execute(  # Close all elections in case of multiple open elections
        update(Election)
        .where(Election.id == election.id, Election.closed_at.is_(None))
        .values(**values)
    )
    result = await session.execute(
        select(Book, func.sum(Vote.weight).label("total_votes"))
        .join(Vote, Book.id == Vote.book_id)
        .where(Vote.election_id == election.id)
        .group_by(Book)
        .order_by(func.sum(Vote.weight).desc())
    )
    all_votes = result.all()
    winner, _ = all_votes[0] if all_votes else (None, 0)
    if winner:
        election.winner = winner.id
        await session.commit()

    embed = discord.Embed(title="Election Results", description="Voting has ended.")
    embed.add_field(name="Winner", value=winner.title if winner else "None", inline=False)
    for idx, (book, votes) in enumerate(all_votes, start=1):
        embed.add_field(name=f"{idx}. {book.title}", value=f"Votes: {votes:.1f}", inline=False)
    await client.get_channel(settings.bookclub_channel_id).send(embed=embed)
    await session.commit()
    return winner
