import discord
from discord.ext import tasks, commands
from loguru import logger

from bot.commands.vote import Ballot
from bot.config import get_settings
from bot.commands.nominate import Nominate
from bot.commands.voting_session import VotingSession

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


@bot.event
async def on_ready():
    await setup_commands()
    synced = await bot.tree.sync()
    logger.info(f"Synced {len(synced)} commands to Discord.")
    logger.info(f"Bot ready as {bot.user}.")
    election_auto_close.start()
    prediction_reminder.start()


@tasks.loop(minutes=60)
async def election_auto_close():
    from bot.background import close_expired_elections

    await close_expired_elections(bot)


@tasks.loop(hours=24)
async def prediction_reminder():
    from bot.background import send_prediction_reminders

    await send_prediction_reminders(bot)


if __name__ == "__main__":
    bot.run(settings.discord_token)
