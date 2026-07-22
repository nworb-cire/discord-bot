import discord
from sqlalchemy import update, select, func


from bot.config import get_settings
from bot.db import Election, Vote, Book
from bot.utils import format_vote_count, utcnow

settings = get_settings()


async def get_election_vote_totals(session, election_id):
    vote_totals = (
        select(
            Vote.book_id,
            func.sum(Vote.weight).label("total_votes"),
        )
        .where(Vote.election_id == election_id)
        .group_by(Vote.book_id)
        .subquery()
    )
    result = await session.execute(
        select(Book, vote_totals.c.total_votes)
        .join(vote_totals, Book.id == vote_totals.c.book_id)
        .order_by(vote_totals.c.total_votes.desc())
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
    channel = client.get_channel(settings.bookclub_channel_id)
    if channel is None:
        channel = await client.fetch_channel(settings.bookclub_channel_id)
    await channel.send(embed=embed)
    return winner
