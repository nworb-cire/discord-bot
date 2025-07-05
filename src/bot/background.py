import discord
from sqlalchemy import select

from bot.config import get_settings
from bot.db import async_session, Election, Prediction
from bot.election import close_and_tally
from bot.utils import utcnow, get_open_election

settings = get_settings()

async def close_expired_elections(bot: discord.Client):
    async with async_session() as session:
        election = await get_open_election(session)
        if not election or election.closes_at > utcnow():
            return
        await close_and_tally(bot, session, election)

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
