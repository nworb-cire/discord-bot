import asyncio
from contextlib import suppress
from datetime import datetime, timezone
from functools import wraps
from string import capwords
from typing import Any, Awaitable, Callable, Optional, TypeVar

import discord
from loguru import logger
from sqlalchemy import select

from bot.config import get_settings
from bot.db import Election


NOMINATION_CANCEL_EMOJI = "❌"
settings = get_settings()


def utcnow() -> datetime:
    """
    Returns the current UTC time as a datetime object.
    """
    return datetime.now(timezone.utc)


async def get_open_election(session):
    return (
        await session.execute(
            select(Election)
            .where(Election.closed_at.is_(None))
            .order_by(Election.opened_at.desc())
        )
    ).scalar_one_or_none()


def short_book_title(title: str) -> str:
    """Return a colon-truncated, capitalized version of a book title."""

    head, *_ = title.split(":", 1)
    shortened = head.strip() or title.strip()
    return capwords(shortened)


def nomination_message_url(message_id: int, guild_id: Optional[int]) -> Optional[str]:
    """Build a link to the original nomination message if guild context is available."""

    if guild_id is None:
        return None
    return f"https://discord.com/channels/{guild_id}/{settings.nom_channel_id}/{message_id}"


class UserFacingError(Exception):
    """An error that should be reported back to the user without logging a stack trace."""

    def __init__(self, message: str, *, ephemeral: bool = True):
        super().__init__(message)
        self.message = message
        self.ephemeral = ephemeral


InteractionFn = Callable[..., Awaitable[Any]]
TFunc = TypeVar("TFunc", bound=InteractionFn)


def handle_interaction_errors(
    default_message: str = "Something went wrong. Please try again.",
) -> Callable[[TFunc], TFunc]:
    """Decorator to ensure interactions always respond, even when errors occur."""

    def decorator(func: TFunc) -> TFunc:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any):
            interaction = _extract_interaction(args, kwargs)
            if interaction is None:
                return await func(*args, **kwargs)

            try:
                return await func(*args, **kwargs)
            except asyncio.TimeoutError as exc:
                logger.warning(
                    "Interaction timed out in {}: {}", func.__qualname__, exc
                )
                await _send_interaction_error(
                    interaction, "Request timed out. Please try again."
                )
            except UserFacingError as exc:
                await _send_interaction_error(
                    interaction, exc.message, ephemeral=exc.ephemeral
                )
            except Exception:
                logger.exception("Unhandled error in {}", func.__qualname__)
                await _send_interaction_error(interaction, default_message)

        return wrapper  # type: ignore[return-value]

    return decorator


def _extract_interaction(
    args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Optional[discord.Interaction]:
    for value in kwargs.values():
        if isinstance(value, discord.Interaction):
            return value
    for value in args:
        if isinstance(value, discord.Interaction):
            return value
    return None


async def _send_interaction_error(
    interaction: discord.Interaction, message: str, *, ephemeral: bool = True
) -> None:
    response = interaction.response
    responded = _interaction_already_handled(response)
    send = interaction.followup.send if responded else response.send_message
    with suppress(Exception):
        await send(message, ephemeral=ephemeral)


def _interaction_already_handled(response: Any) -> bool:
    checker = getattr(response, "is_done", None)
    if callable(checker):
        return bool(checker())
    if hasattr(response, "deferred") and getattr(response, "deferred"):
        return True
    messages = getattr(response, "messages", None)
    if messages and len(messages) > 0:
        return True
    return False
