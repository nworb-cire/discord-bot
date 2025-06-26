import re
import httpx
from bs4 import BeautifulSoup
from loguru import logger


ASIN_RE = re.compile(r"/([A-Z0-9]{10})(?:[/?]|$)")


def extract_asin(url: str) -> str | None:
    m = ASIN_RE.search(url)
    return m.group(1) if m else None


async def scrape_amazon_product(asin: str) -> dict:
    url = f"https://www.amazon.com/dp/{asin}"
    logger.debug("Scraping {}", url)
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(url, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

    title = soup.select_one("#productTitle")
    author = soup.select_one(".author a")
    # paperback = soup.select_one("span.a-size-base.a-color-price.a-text-bold")
    kindle = soup.select_one("a[href*='kindle'] .a-color-price")
    audible = soup.select_one("a[href*='audible'] .a-color-price")

    def price_to_float(tag):
        if not tag:
            return None
        return float(tag.text.strip().replace("$", "").replace(",", ""))

    return {
        "title": title.text.strip() if title else "Unknown",
        "author": author.text.strip() if author else "Unknown",
        "price_paperback": -1.0,
        "price_kindle": price_to_float(kindle),
        "price_audible": price_to_float(audible),
    }
