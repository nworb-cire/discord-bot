import pytest

from bot.goodreads import (
    GOODREADS_BASE_URL,
    GoodreadsLookup,
    GoodreadsRating,
    _search_result_url,
    format_goodreads_rating,
    parse_goodreads_rating,
)


def test_parse_goodreads_rating_from_json_ld():
    page = """
    <script type="application/ld+json">
      {"@context": "https://schema.org", "@type": "Book",
       "aggregateRating": {"@type": "AggregateRating", "ratingValue": 3.74,
       "ratingCount": 4720}}
    </script>
    """

    assert parse_goodreads_rating(page) == GoodreadsRating(3.74, 4720)


def test_parse_goodreads_rating_ignores_invalid_data():
    page = '<script type="application/ld+json">{"@type":"Book"}</script>'

    assert parse_goodreads_rating(page) is None


def test_format_goodreads_rating_rounds_and_formats_count():
    assert format_goodreads_rating(3.74, 4720) == "3.7⭐️ (4,720)"
    assert format_goodreads_rating(None, 4720) is None


def test_search_result_url_prefers_an_exact_title():
    page = """
    <a class="bookTitle" href="/book/show/110.The_Road_to_Dune"><span>The Road to Dune</span></a>
    <a class="bookTitle" href="/book/show/44767458-dune"><span>Dune</span></a>
    """

    assert _search_result_url(page, "Dune") == "/book/show/44767458-dune"


@pytest.mark.asyncio
async def test_lookup_uses_isbn_when_available(monkeypatch):
    urls = []

    async def get_page(_client, url):
        urls.append(url)
        return """
        <script type="application/ld+json">
          {"@type":"Book", "aggregateRating":{"ratingValue":4.29,"ratingCount":1700705}}
        </script>
        """

    monkeypatch.setattr(GoodreadsLookup, "_get_page", staticmethod(get_page))

    rating = await GoodreadsLookup().lookup(
        title="Dune", author="Frank Herbert", isbn="9780441172719"
    )

    assert rating == GoodreadsRating(4.29, 1700705)
    assert urls == [f"{GOODREADS_BASE_URL}/book/isbn/9780441172719"]


@pytest.mark.asyncio
async def test_lookup_searches_by_title_when_isbn_is_missing(monkeypatch):
    urls = []

    async def get_page(_client, url):
        urls.append(url)
        if "/search?" in url:
            return """
            <a class="bookTitle" href="/book/show/110.The_Road_to_Dune"><span>The Road to Dune</span></a>
            <a class="bookTitle" href="/book/show/44767458-dune"><span>Dune</span></a>
            """
        return """
        <script type="application/ld+json">
          {"@type":"Book", "aggregateRating":{"ratingValue":4.29,"ratingCount":1700705}}
        </script>
        """

    monkeypatch.setattr(GoodreadsLookup, "_get_page", staticmethod(get_page))

    rating = await GoodreadsLookup().lookup(
        title="Dune", author="Frank Herbert", isbn=None
    )

    assert rating == GoodreadsRating(4.29, 1700705)
    assert urls[-1] == f"{GOODREADS_BASE_URL}/book/show/44767458-dune"
