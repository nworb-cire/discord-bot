"""Presentation helpers for metadata stored with books."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from bot.goodreads import format_goodreads_rating


class BookWithMetadata(Protocol):
    length: int | None
    goodreads_rating: float | None
    goodreads_rating_count: int | None


@dataclass(frozen=True, slots=True)
class BookMetadata:
    page_count: int | None
    goodreads_rating: float | None
    goodreads_rating_count: int | None

    @classmethod
    def from_book(cls, book: BookWithMetadata) -> "BookMetadata":
        """Extract displayable metadata from a persisted book."""
        return cls(
            page_count=getattr(book, "length", None),
            goodreads_rating=getattr(book, "goodreads_rating", None),
            goodreads_rating_count=getattr(book, "goodreads_rating_count", None),
        )

    def format(self) -> str | None:
        """Return the compact metadata line used in ballot-related embeds."""
        details: list[str] = []
        goodreads_text = format_goodreads_rating(
            self.goodreads_rating, self.goodreads_rating_count
        )
        if goodreads_text:
            details.append(f"Goodreads: {goodreads_text}")
        if self.page_count:
            details.append(f"{self.page_count} pages")
        return " · ".join(details) or None


def format_book_metadata(book: BookWithMetadata) -> str | None:
    """Format a book's persisted Goodreads rating and page count."""
    return BookMetadata.from_book(book).format()
