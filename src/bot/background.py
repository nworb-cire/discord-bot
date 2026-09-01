import asyncio

import discord
from loguru import logger
from sqlalchemy import select

from bot.calendar_sync import DiscordGoogleCalendarSync, SyncError
from bot.recurring_discord_events import (
    RecurringDiscordEventCreator,
    RecurringEventError,
)
from bot.config import get_settings
from bot.db import async_session, Prediction
from bot.election import close_and_tally
from bot.prediction_evidence import (
    PredictionEvidence,
    PredictionEvidenceLookupError,
    find_prediction_evidence,
)
from bot.utils import MOUNTAIN, get_open_election, utcnow

settings = get_settings()


async def close_expired_elections(bot: discord.Client):
    async with async_session() as session:
        election = await get_open_election(session)
        if not election or election.closes_at > utcnow():
            return
        await close_and_tally(bot, session, election)


async def send_prediction_reminders(bot: discord.Client):
    now_local = utcnow().astimezone(MOUNTAIN)
    cutoff = now_local.replace(tzinfo=None)
    async with async_session() as session:
        result = await session.execute(
            select(Prediction).where(
                Prediction.due_at <= cutoff, Prediction.reminded.is_(False)
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
            try:
                evidence = await find_prediction_evidence(p)
            except PredictionEvidenceLookupError:
                logger.exception(
                    "Prediction evidence lookup failed prediction_id={}",
                    getattr(p, "id", None),
                )
                evidence = []

            link = (
                f"https://discord.com/channels/{guild_id}/{channel.id}/{p.message_id}"
                if guild_id is not None and p.message_id is not None
                else None
            )
            created_timestamp = int(p.created_at.timestamp())
            lines = [
                "Reminder to adjudicate prediction made by "
                f"<@{p.predictor_discord_id}> on <t:{created_timestamp}:f>:",
                f"> {p.text}",
            ]
            if link:
                lines.append(link)
            lines.extend(_format_prediction_evidence(evidence))
            await channel.send("\n".join(lines))
            p.reminded = True
        await session.commit()


def _format_prediction_evidence(evidence: list[PredictionEvidence]) -> list[str]:
    if not evidence:
        return ["", "No conclusive evidence found by quick search."]

    lines = ["", "Conclusive evidence found:"]
    for item in evidence:
        direction = (
            "Supporting" if item.direction == "supporting" else "Contradictory"
        )
        lines.append(f"- **{direction}:** {item.summary}")
        lines.append(f"  {item.url}")
    return lines


async def run_calendar_sync():
    if not DiscordGoogleCalendarSync.is_configured(settings):
        return

    try:
        await asyncio.to_thread(DiscordGoogleCalendarSync(settings).run)
    except SyncError:
        logger.exception("Calendar sync failed.")
    except Exception:
        logger.exception("Calendar sync failed unexpectedly.")


async def run_recurring_event_creation():
    try:
        await asyncio.to_thread(RecurringDiscordEventCreator(settings).run)
    except RecurringEventError:
        logger.exception("Recurring event creation failed.")
    except Exception:
        logger.exception("Recurring event creation failed unexpectedly.")
