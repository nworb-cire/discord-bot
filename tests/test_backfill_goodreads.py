from types import SimpleNamespace

import pytest

from bot.backfill_goodreads import backfill_goodreads_ratings
from bot.goodreads import GoodreadsRating


class _ScalarResult:
    def __init__(self, books):
        self.books = books

    def all(self):
        return self.books


class _Session:
    def __init__(self, books):
        self.books = books
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def scalars(self, _statement):
        return _ScalarResult(self.books)

    async def commit(self):
        self.commits += 1


class _Lookup:
    def __init__(self, ratings):
        self.ratings = ratings
        self.calls = []

    async def lookup(self, **kwargs):
        self.calls.append(kwargs)
        return self.ratings[kwargs["title"]]


@pytest.mark.asyncio
async def test_backfill_updates_available_ratings_and_rate_limits_requests():
    books = [
        SimpleNamespace(
            id=1,
            title="First Book",
            primary_author="First Author",
            isbn_13="9780000000001",
            isbn_10=None,
            goodreads_rating=None,
            goodreads_rating_count=None,
        ),
        SimpleNamespace(
            id=2,
            title="Second Book",
            primary_author="Second Author",
            isbn_13=None,
            isbn_10="0000000002",
            goodreads_rating=None,
            goodreads_rating_count=None,
        ),
        SimpleNamespace(
            id=3,
            title="Missing Book",
            primary_author=None,
            isbn_13=None,
            isbn_10=None,
            goodreads_rating=None,
            goodreads_rating_count=None,
        ),
    ]
    session = _Session(books)
    lookup = _Lookup(
        {
            "First Book": GoodreadsRating(4.25, 25),
            "Second Book": GoodreadsRating(3.75, None),
            "Missing Book": None,
        }
    )
    delays = []

    result = await backfill_goodreads_ratings(
        sessionmaker=lambda: session,
        lookup=lookup,
        interval_seconds=60,
        sleep=lambda seconds: _record_delay(delays, seconds),
    )

    assert result.attempted == 3
    assert result.updated == 2
    assert result.unavailable == 1
    assert result.failed == 0
    assert session.commits == 2
    assert delays == [60, 60]
    assert books[0].goodreads_rating == 4.25
    assert books[0].goodreads_rating_count == 25
    assert books[1].goodreads_rating == 3.75
    assert books[1].goodreads_rating_count is None
    assert lookup.calls[1]["isbn"] == "0000000002"
    assert lookup.calls[2]["author"] == ""


async def _record_delay(delays, seconds):
    delays.append(seconds)
