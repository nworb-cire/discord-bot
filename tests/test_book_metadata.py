from types import SimpleNamespace

from bot.book_metadata import BookMetadata, format_book_metadata


def test_format_book_metadata_includes_goodreads_and_page_count():
    book = SimpleNamespace(
        length=321, goodreads_rating=3.74, goodreads_rating_count=4720
    )

    assert format_book_metadata(book) == "Goodreads: 3.7⭐️ (4,720) · 321 pages"


def test_book_metadata_format_supports_each_value_independently():
    assert (
        BookMetadata(
            page_count=321, goodreads_rating=None, goodreads_rating_count=None
        ).format()
        == "321 pages"
    )
    assert (
        BookMetadata(
            page_count=None, goodreads_rating=4.0, goodreads_rating_count=None
        ).format()
        == "Goodreads: 4.0⭐️"
    )
    assert (
        BookMetadata(
            page_count=None, goodreads_rating=None, goodreads_rating_count=None
        ).format()
        is None
    )
