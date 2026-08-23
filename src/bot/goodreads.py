"""Best-effort Goodreads rating lookup for nominated books."""

from __future__ import annotations

from dataclasses import dataclass
import html
import json
import re
from typing import Any
from urllib.parse import quote, urljoin

import httpx


GOODREADS_BASE_URL = "https://www.goodreads.com"
GOODREADS_USER_AGENT = (
    "Mozilla/5.0 (compatible; BookClubBot/1.0; "
    "+https://github.com/nworb-cire/bookclub-bot)"
)
JSON_LD_RE = re.compile(
    r'<script\s+type=["\']application/ld\+json["\'][^>]*>(?P<payload>.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
SEARCH_RESULT_RE = re.compile(
    r'<a\s+class=["\']bookTitle["\'][^>]*href=["\'](?P<url>/book/show/[^"\']+)["\'][^>]*>'
    r"\s*(?:<span[^>]*>)?(?P<title>.*?)(?:</span>)?\s*</a>",
    re.IGNORECASE | re.DOTALL,
)
TAG_RE = re.compile(r"<[^>]+>")
NON_ALPHANUMERIC_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class GoodreadsRating:
    score: float
    rating_count: int | None


def format_goodreads_rating(
    score: float | None, rating_count: int | None
) -> str | None:
    """Return the compact score shown in Discord embeds."""
    if score is None:
        return None
    result = f"{score:.1f}⭐️"
    if rating_count is not None:
        result += f" ({rating_count:,})"
    return result


def parse_goodreads_rating(page: str) -> GoodreadsRating | None:
    """Extract a book's aggregate rating from its public JSON-LD metadata."""
    for match in JSON_LD_RE.finditer(page):
        try:
            data = json.loads(html.unescape(match.group("payload")))
        except json.JSONDecodeError:
            continue
        for item in _json_ld_items(data):
            if item.get("@type") != "Book":
                continue
            aggregate_rating = item.get("aggregateRating")
            if not isinstance(aggregate_rating, dict):
                continue
            try:
                score = float(aggregate_rating["ratingValue"])
            except (KeyError, TypeError, ValueError):
                continue
            if not 0 < score <= 5:
                continue
            rating_count = _parse_rating_count(aggregate_rating.get("ratingCount"))
            return GoodreadsRating(score=score, rating_count=rating_count)
    return None


def _json_ld_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        graph = data.get("@graph")
        if isinstance(graph, list):
            return [item for item in graph if isinstance(item, dict)]
        return [data]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _parse_rating_count(value: Any) -> int | None:
    try:
        rating_count = int(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return rating_count if rating_count >= 0 else None


def _normalize_title(value: str) -> str:
    return NON_ALPHANUMERIC_RE.sub(" ", value.casefold()).strip()


def _search_result_url(page: str, title: str) -> str | None:
    """Prefer an exact-title Goodreads search result, then use the first result."""
    matches = list(SEARCH_RESULT_RE.finditer(page))
    if not matches:
        return None
    expected_title = _normalize_title(title)
    for match in matches:
        result_title = _normalize_title(
            TAG_RE.sub("", html.unescape(match.group("title")))
        )
        if result_title == expected_title:
            return html.unescape(match.group("url"))
    return html.unescape(matches[0].group("url"))


class GoodreadsLookup:
    def __init__(self, timeout_seconds: float = 10.0):
        self.timeout_seconds = timeout_seconds

    async def lookup(
        self,
        *,
        title: str,
        author: str,
        isbn: str | None,
    ) -> GoodreadsRating | None:
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": GOODREADS_USER_AGENT},
        ) as client:
            if isbn:
                page = await self._get_page(
                    client, f"{GOODREADS_BASE_URL}/book/isbn/{quote(isbn)}"
                )
                return parse_goodreads_rating(page)

            search_url = (
                f"{GOODREADS_BASE_URL}/search?q="
                f"{quote(f'{title} {author}')}&search_type=books"
            )
            search_page = await self._get_page(client, search_url)
            result_url = _search_result_url(search_page, title)
            if result_url is None:
                return None
            book_page = await self._get_page(
                client, urljoin(GOODREADS_BASE_URL, result_url)
            )
            return parse_goodreads_rating(book_page)

    @staticmethod
    async def _get_page(client: httpx.AsyncClient, url: str) -> str:
        response = await client.get(url)
        response.raise_for_status()
        return response.text
