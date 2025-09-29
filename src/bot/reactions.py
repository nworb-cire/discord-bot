from __future__ import annotations

import contextlib
import logging
from typing import Optional

import discord
from sqlalchemy import func, select

from bot.config import get_settings
from bot.db import Election, Vote, async_session


log = logging.getLogger(__name__)
settings = get_settings()

DiscordHTTPException = getattr(discord, "HTTPException", Exception)
DiscordForbidden = getattr(discord, "Forbidden", Exception)
DiscordNotFound = getattr(discord, "NotFound", Exception)

_SUPPRESSED_EXCEPTIONS = (
    DiscordHTTPException,
    DiscordForbidden,
    DiscordNotFound,
)


NUMBER_EMOJIS: dict[int, str] = {
    0: "0️⃣",
    1: "1️⃣",
    2: "2️⃣",
    3: "3️⃣",
    4: "4️⃣",
    5: "5️⃣",
    6: "6️⃣",
    7: "7️⃣",
    8: "8️⃣",
    9: "9️⃣",
    10: "🔟",
}

PLUS_EMOJI = "➕"


async def _fetch_ballot_message(
    client: discord.Client | discord.AutoShardedClient,
    message_id: int,
) -> Optional[discord.Message]:
    """Fetch the ballot message for the current election."""

    channel = client.get_channel(settings.bookclub_channel_id)
    if channel is None:
        with contextlib.suppress(*_SUPPRESSED_EXCEPTIONS):
            channel = await client.fetch_channel(settings.bookclub_channel_id)
    if channel is None:
        log.warning(
            "Unable to locate book club channel %s when updating vote reactions",
            settings.bookclub_channel_id,
        )
        return None
    fetch = getattr(channel, "fetch_message", None)
    if fetch is None:
        log.warning("Channel %s does not support fetch_message", channel)
        return None
    try:
        return await fetch(message_id)
    except DiscordNotFound:
        log.warning("Ballot message %s no longer exists", message_id)
    except (DiscordForbidden, DiscordHTTPException):
        log.exception("Failed to fetch ballot message %s", message_id)
    return None


async def _set_vote_reaction(
    client: discord.Client | discord.AutoShardedClient,
    message: discord.Message,
    total_voters: int,
) -> bool:
    """Remove bot reactions and add emojis reflecting the voter count."""

    if client.user is None:
        log.debug("Client user is not ready; skipping reaction update")
        return False
    bot_user = client.user
    total_voters = int(total_voters)
    display_count = max(0, min(total_voters, 10))
    emojis = [NUMBER_EMOJIS.get(display_count, NUMBER_EMOJIS[10])]
    freeze = total_voters >= 11
    if freeze:
        emojis.append(PLUS_EMOJI)

    for reaction in list(getattr(message, "reactions", [])):
        if getattr(reaction, "me", False):
            with contextlib.suppress(*_SUPPRESSED_EXCEPTIONS):
                await reaction.remove(bot_user)

    for emoji in emojis:
        with contextlib.suppress(*_SUPPRESSED_EXCEPTIONS):
            await message.add_reaction(emoji)

    return freeze


async def update_election_vote_reaction(
    client: discord.Client | discord.AutoShardedClient, election_id: int
) -> None:
    """Recompute voter totals and update the ballot message reaction to match."""

    async with async_session() as session:
        election = await session.get(Election, election_id)
        if not election or not election.ballot_message_id:
            return
        if getattr(election, "vote_reaction_frozen", False):
            return
        result = await session.execute(
            select(func.count(func.distinct(Vote.voter_discord_id))).where(
                Vote.election_id == election_id
            )
        )
        total_voters = result.scalar_one_or_none() or 0
        ballot_message_id = election.ballot_message_id

    message = await _fetch_ballot_message(client, ballot_message_id)
    if message is None:
        return

    freeze = await _set_vote_reaction(client, message, int(total_voters))

    if freeze:
        async with async_session() as session:
            election = await session.get(Election, election_id)
            if election and not getattr(election, "vote_reaction_frozen", False):
                election.vote_reaction_frozen = True
                session.add(election)
                await session.commit()
