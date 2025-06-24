# tests/test_nominate.py
import pytest
from bot.scraper import extract_asin


@pytest.mark.parametrize(
    "url,asin",
    [
        ("https://www.amazon.com/dp/B003JTHWKU", "B003JTHWKU"),
        ("https://www.amazon.com/gp/product/B00005K3O2", "B00005K3O2"),
        ("https://amazon.com/some/path", None),
    ],
)
def test_extract_asin(url, asin):
    assert extract_asin(url) == asin
