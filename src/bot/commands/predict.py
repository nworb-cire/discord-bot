from __future__ import annotations

from datetime import timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import get_settings
from bot.db import Prediction, async_session
from bot.utils import (
    UserFacingError,
    handle_interaction_errors,
    parse_due_datetime,
)

settings = get_settings()


def _normalize_probability(probability: Optional[float]) -> Optional[float]:
    if probability is None:
        return None
    if probability < 0:
        raise UserFacingError("Probability must be non-negative.")
    percent = probability * 100 if probability <= 1 else probability
    if percent > 100:
        raise UserFacingError("Probability cannot exceed 100%.")
    return round(percent, 1)


class Predict(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="predict", description="Record a prediction and schedule a reminder."
    )
    @app_commands.describe(
        due="Due date for judging (YYYY-MM-DD or ISO datetime).",
        text="Prediction text.",
        probability="Optional probability (0-1 or 0-100).",
    )
    @handle_interaction_errors()
    async def predict(
        self,
        interaction: discord.Interaction,
        due: str,
        text: str,
        probability: Optional[float] = None,
    ) -> None:
        try:
            due_at_local = parse_due_datetime(due)
        except ValueError as exc:
            raise UserFacingError(str(exc)) from exc

        probability_percent = _normalize_probability(probability)
        prediction_text = text.strip()
        if not prediction_text:
            raise UserFacingError("Prediction text cannot be empty.")

        channel = self.bot.get_channel(settings.predictions_channel_id)
        if channel is None:
            channel = await self.bot.fetch_channel(settings.predictions_channel_id)

        due_timestamp = int(due_at_local.astimezone(timezone.utc).timestamp())
        lines = [
            f"**Prediction from {interaction.user.mention}**",
            f"> {prediction_text}",
            f"Due: <t:{due_timestamp}:f> (<t:{due_timestamp}:R>)",
        ]
        if probability_percent is not None:
            lines.append(f"Confidence: {probability_percent:.1f}%")
        message = await channel.send("\n".join(lines))

        async with async_session() as session:
            record = Prediction(
                predictor_discord_id=interaction.user.id,
                text=prediction_text,
                odds=probability_percent,
                due_at=due_at_local.replace(tzinfo=None),
                message_id=message.id,
            )
            session.add(record)
            await session.commit()

        link = getattr(message, "jump_url", None)
        if link is None:
            guild_id = getattr(channel, "guild", None)
            guild_id = getattr(guild_id, "id", None)
            if guild_id is not None:
                link = (
                    f"https://discord.com/channels/{guild_id}/{channel.id}/{message.id}"
                )

        response_lines = [f"Prediction scheduled for <t:{due_timestamp}:D>."]
        if link:
            response_lines.append(f"View it [here]({link}).")
        await interaction.response.send_message(
            " ".join(response_lines), ephemeral=True
        )
