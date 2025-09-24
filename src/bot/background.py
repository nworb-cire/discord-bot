import discord
from sqlalchemy import select

from bot.config import get_settings
from bot.db import async_session, Prediction
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
        result = await session.execute(
            select(Prediction).where(
                Prediction.due_date == today, Prediction.reminded.is_(False)
            )
        )
        preds = list(result.scalars())
        if not preds:
            return

        channel = bot.get_channel(settings.predictions_channel_id)
        if channel is None:
            channel = await bot.fetch_channel(settings.predictions_channel_id)

        guild_id = getattr(channel, "guild", None)
        guild_id = getattr(guild_id, "id", None)
        for p in preds:
            link = (
                f"https://discord.com/channels/{guild_id}/{channel.id}/{p.message_id}"
                if guild_id is not None and p.message_id is not None
                else None
            )
            lines = [
                "Reminder to adjudicate prediction:",
                f"> {p.text}",
            ]
            if link:
                lines.append(link)
            await channel.send("\n".join(lines))
            p.reminded = True
        await session.commit()
