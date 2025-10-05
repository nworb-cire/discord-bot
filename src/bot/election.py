import discord
from sqlalchemy import update, select, func


from bot.config import get_settings
from bot.db import Election, Vote, Book
from bot.utils import format_vote_count, utcnow

settings = get_settings()


async def get_election_vote_totals(session, election_id):
    result = await session.execute(
        select(Book, func.sum(Vote.weight).label("total_votes"))
        .join(Vote, Book.id == Vote.book_id)
        .where(Vote.election_id == election_id)
        .group_by(Book)
        .order_by(func.sum(Vote.weight).desc())
    )
    return [(book, float(total or 0.0)) for book, total in result.all()]


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
    all_votes = await get_election_vote_totals(session, election.id)
    winner, _ = all_votes[0] if all_votes else (None, 0)
    if winner:
        election.winner = winner.id
        await session.commit()

    embed = discord.Embed(title="Election Results", description="Voting has ended.")
    embed.add_field(
        name="Winner", value=winner.title if winner else "None", inline=False
    )
    for idx, (book, votes) in enumerate(all_votes, start=1):
        embed.add_field(
            name=f"{idx}. {book.title}",
            value=f"Votes: {format_vote_count(votes)}",
            inline=False,
        )
    await client.get_channel(settings.bookclub_channel_id).send(embed=embed)
    await session.commit()
    return winner
