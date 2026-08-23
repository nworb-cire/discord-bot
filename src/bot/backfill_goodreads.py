"""Rate-limit-safe Goodreads rating backfill for existing books."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings, get_settings
from bot.db import Book, get_engine, get_sessionmaker
from bot.goodreads import GoodreadsLookup


@dataclass(frozen=True, slots=True)
class BackfillResult:
    attempted: int
    updated: int
    unavailable: int
    failed: int


async def backfill_goodreads_ratings(
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    lookup: GoodreadsLookup,
    interval_seconds: float,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> BackfillResult:
    """Backfill each currently unscored book, committing each successful result."""
    if interval_seconds < 0:
        raise ValueError("interval_seconds must be non-negative")

    async with sessionmaker() as session:
        books = list(
            (
                await session.scalars(
                    select(Book)
                    .where(Book.goodreads_rating.is_(None))
                    .order_by(Book.id)
                )
            ).all()
        )

        updated = 0
        unavailable = 0
        failed = 0
        for index, book in enumerate(books):
            try:
                rating = await lookup.lookup(
                    title=book.title,
                    author=book.primary_author or "",
                    isbn=book.isbn_13 or book.isbn_10,
                )
            except Exception as exc:
                failed += 1
                logger.warning(
                    "Goodreads backfill failed book_id={} title={!r} error_type={}",
                    book.id,
                    book.title,
                    type(exc).__name__,
                )
            else:
                if rating is None:
                    unavailable += 1
                    logger.warning(
                        "Goodreads backfill found no rating book_id={} title={!r}",
                        book.id,
                        book.title,
                    )
                else:
                    book.goodreads_rating = rating.score
                    book.goodreads_rating_count = rating.rating_count
                    await session.commit()
                    updated += 1
                    logger.info(
                        "Goodreads backfill updated book_id={} title={!r} score={} rating_count={}",
                        book.id,
                        book.title,
                        rating.score,
                        rating.rating_count,
                    )

            if index < len(books) - 1:
                await sleep(interval_seconds)

    return BackfillResult(
        attempted=len(books),
        updated=updated,
        unavailable=unavailable,
        failed=failed,
    )


async def run(settings: Settings | None = None) -> BackfillResult:
    """Run the backfill with the configured production dependencies."""
    settings = settings or get_settings()
    return await backfill_goodreads_ratings(
        sessionmaker=get_sessionmaker(),
        lookup=GoodreadsLookup(
            timeout_seconds=settings.goodreads_lookup_timeout_seconds
        ),
        interval_seconds=settings.goodreads_backfill_interval_seconds,
    )


async def main() -> None:
    try:
        result = await run()
        logger.info(
            "Goodreads backfill complete attempted={} updated={} unavailable={} failed={}",
            result.attempted,
            result.updated,
            result.unavailable,
            result.failed,
        )
    finally:
        await get_engine().dispose()


if __name__ == "__main__":
    asyncio.run(main())
