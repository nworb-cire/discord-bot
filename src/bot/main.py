import discord
from discord.ext import tasks, commands
from loguru import logger

from bot.background import (
    close_expired_elections,
    run_calendar_sync,
    run_recurring_event_creation,
    send_prediction_reminders,
)
from bot.config import get_settings
from bot.commands.predict import Predict
from bot.commands.vote import Ballot
from bot.commands.nominate import Nominate
from bot.commands.voting_session import VotingSession
from bot.utils import MOUNTAIN, utcnow

settings = get_settings()
bot = commands.Bot(
    command_prefix="",
    intents=discord.Intents.default(),
    help_command=None,
)


async def setup_commands():
    await bot.add_cog(Nominate(bot))
    await bot.add_cog(VotingSession(bot))
    await bot.add_cog(Ballot(bot))
    await bot.add_cog(Predict(bot))


@bot.event
async def on_ready():
    await setup_commands()
    synced = await bot.tree.sync()
    logger.info(f"Synced {len(synced)} commands to Discord.")
    logger.info(f"Bot ready as {bot.user}.")
    election_auto_close.start()
    prediction_reminder.start()
    calendar_sync.start()
    recurring_event_creation.start()


@tasks.loop(minutes=60)
async def election_auto_close():
    await close_expired_elections(bot)


@tasks.loop(minutes=1)
async def prediction_reminder():
    await send_prediction_reminders(bot)


@tasks.loop(minutes=30)
async def calendar_sync():
    await run_calendar_sync()


@tasks.loop(hours=6)
async def recurring_event_creation():
    if utcnow().astimezone(MOUNTAIN).day != 1:
        return
    await run_recurring_event_creation()


if __name__ == "__main__":
    bot.run(settings.discord_token)
