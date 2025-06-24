from sqlalchemy import select, update
from bot.db import async_session, Election, Prediction, Book, Vote
from bot.utils import utcnow
from bot.config import get_settings
import discord

settings = get_settings()


async def close_expired_elections(bot: discord.Client):
    now = utcnow()
    async with async_session() as session:
        elections = (
            await session.execute(select(Election).where(Election.closed_at.is_(None), Election.closes_at <= now))
        ).scalars()
        for election in elections:
            election.closed_at = now
            await session.commit()

            winner_vote = (
                await session.execute(
                    select(Vote.book_id, Vote.weight)
                    .where(Vote.election_id == election.id)
                    .group_by(Vote.book_id)
                    .order_by(Vote.weight.desc())
                    .limit(1)
                )
            ).first()
            if not winner_vote:
                continue
            winner_book = await session.get(Book, winner_vote.book_id)
            channel = bot.get_channel(settings.results_channel_id)
            await channel.send(f"Election closed! Winner: **{winner_book.title}**")


async def send_prediction_reminders(bot: discord.Client):
    today = utcnow().date()
    async with async_session() as session:
        preds = (
            await session.execute(
                select(Prediction).where(Prediction.due_date == today, Prediction.reminded.is_(False))
            )
        ).scalars()
        channel = bot.get_channel(settings.predictions_channel_id)
        for p in preds:
            await channel.send(f"Reminder to adjudicate prediction:\n> {p.text}")
            p.reminded = True
        await session.commit()
