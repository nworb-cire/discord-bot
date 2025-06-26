import discord
from discord.ext import tasks
from loguru import logger
from bot.config import get_settings
from bot.commands.nominate import Nominate
from bot.commands.open_voting import OpenVoting
# (other command classes would be imported similarly)

settings = get_settings()
bot = discord.Client(intents=discord.Intents.default())
tree = discord.app_commands.CommandTree(bot)


async def setup_commands():
    tree.add_command(Nominate())
    # tree.add_command(OpenVoting._open)
    # add other command registrations here


@bot.event
async def on_ready():
    await setup_commands()
    synced = await tree.sync(guild=discord.Object(id=1378386449023504455))
    logger.info(f"Synced {len(synced)} commands to Discord.")
    logger.info(f"Bot ready as {bot.user}.")
    election_auto_close.start()
    prediction_reminder.start()


@tasks.loop(seconds=60)
async def election_auto_close():
    from bot.background import close_expired_elections

    await close_expired_elections(bot)


@tasks.loop(hours=24)
async def prediction_reminder():
    from bot.background import send_prediction_reminders

    await send_prediction_reminders(bot)


if __name__ == "__main__":
    bot.run(settings.discord_token)
