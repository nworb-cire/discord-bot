from datetime import datetime, timezone

from sqlalchemy import select

from bot.db import Election


def utcnow() -> datetime:
    """
    Returns the current UTC time as a datetime object.
    """
    return datetime.now(timezone.utc)


async def get_open_election(session):
    return (await session.execute(
        select(Election)
        .where(Election.closed_at.is_(None))
        .order_by(Election.opened_at.desc())
    )).scalar_one_or_none()
