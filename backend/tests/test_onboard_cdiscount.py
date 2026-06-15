"""TDD test: verify cdiscount crawler routes through BaseCrawler.make_fetcher
and increments counter.api_calls via the unified fetch layer.

Before migration: CdiscountCrawler uses raw curl_cffi Session directly;
make_fetcher is never called; counter.api_calls stays 0.
After migration: every HTTP GET/POST goes through the unified CrawlerFetcher;
counter.api_calls is incremented on each successful fetch.

cdiscount specifics:
- Baleen anti-bot requires a GET→POST→GET handshake with cookie management
- _warmup_baleen: GET home (stub) → POST check → GET home (real page)
- _discover: GET list pages (BFS)
- _fetch_pdp: GET product pages
- POST uses fetcher.post(url, data=, headers=)
"""
from __future__ import annotations

import json

import pytest

from app.fetching import FetchResult
from app.models import Site

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixture HTML helpers
# ---------------------------------------------------------------------------

# Baleen challenge stub: contains __blnChallengeStore JSON
_BALEEN_COOKIE_NAME = "visit_baleen_test"
_BALEEN_COOKIE_VALUE = "test_token_123"
_BALEEN_STORE = {
    "cookie": {
        "name": _BALEEN_COOKIE_NAME,
        "value": _BALEEN_COOKIE_VALUE,
    },
    "checkChallengeParams": {
        "bot_category": "human",
        "request_fate": "allow",
    },
}
_BALEEN_STUB_HTML = (
    "<html><head></head><body>"
    f"<script>var __blnChallengeStore={json.dumps(_BALEEN_STORE)};</script>"
    # Padding so it has some content but is NOT > 50KB (still a challenge page)
    + " " * 100
    + "</body></html>"
)

# Real homepage HTML after Baleen is solved (>50KB, no blnChallengeStore)
_LIST_PATH = "/high-tech/tv-home-cinema/televiseurs/l-37302.html"
_PROD_PATH = "/high-tech/tv/samsung-tv-55/f-10703-sam55ue8085.html"
_SKU = "sam55ue8085"

_HOME_HTML = (
    "<html><body>"
    # List URL and product URL seeds
    f'<a href="{_LIST_PATH}">TV</a>'
    f'<a href="{_PROD_PATH}">Samsung TV</a>'
    + " " * 60_000  # > 50KB, no blnChallengeStore → real page
    + "</body></html>"
)

# List page HTML: contains product URL
_LIST_HTML = (
    "<html><body>"
    f'<a href="{_PROD_PATH}">Samsung TV</a>'
    + " " * 60_000
    + "</body></html>"
)

# PDP page: JSON-LD Product block + BreadcrumbList
_PRODUCT_JSONLD = {
    "@context": "https://schema.org",
    "@type": "product",
    "name": "Samsung TV 55 pouces UHD",
    "description": "Télévision UHD Samsung 55 pouces.",
    "sku": _SKU,
    "image": ["https://cdiscount-image.com/test.jpg"],
    "brand": {"@type": "Brand", "name": "Samsung"},
    "offers": {
        "@type": "Offer",
        "price": "649.99",
        "priceCurrency": "EUR",
        "availability": "https://schema.org/InStock",
    },
    "aggregateRating": {
        "@type": "AggregateRating",
        "ratingValue": "4.5",
        "ratingCount": "230",
    },
}
_BREADCRUMB_JSONLD = {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    "itemListElement": [
        {"@type": "ListItem", "position": 1,
         "item": {"name": "Accueil", "@id": "https://www.cdiscount.com/"}},
        {"@type": "ListItem", "position": 2,
         "item": {"name": "High-Tech", "@id": "https://www.cdiscount.com/high-tech/"}},
        {"@type": "ListItem", "position": 3,
         "item": {"name": "TV", "@id": "https://www.cdiscount.com/high-tech/tv/"}},
    ],
}

_PDP_HTML = (
    "<html><head>"
    '<script type="application/ld+json">' + json.dumps(_PRODUCT_JSONLD) + "</script>"
    '<script type="application/ld+json">' + json.dumps(_BREADCRUMB_JSONLD) + "</script>"
    + "</head><body>"
    + " " * 30_000  # > 20KB, no blnChallengeStore
    + "</body></html>"
)

_PROD_URL = f"https://www.cdiscount.com{_PROD_PATH}"


def _site() -> Site:
    s = Site()
    s.site = "cdiscount"
    s.url = "https://www.cdiscount.com"
    s.country = "FR"
    s.proxy_tier = "none"
    s.platform = "cdiscount"
    s.brand = None
    return s


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_cdiscount_routes_through_make_fetcher_and_counts(monkeypatch):
    """After migration, all HTTP calls go through make_fetcher → counter increments."""
    from app.crawlers.cdiscount import CdiscountCrawler

    crawler = CdiscountCrawler(_site())
    crawler.limit = 1

    calls: list[tuple[str, str]] = []  # (method, url)
    post_call_count = 0

    def fake_get(url: str, **kw) -> FetchResult:
        calls.append(("GET", url))
        crawler.counter.api_calls += 1
        # First GET to home → Baleen challenge stub
        if url == "https://www.cdiscount.com/" and len([c for c in calls if c == ("GET", url)]) <= 1:
            text = _BALEEN_STUB_HTML
        # Second GET to home (after POST check) → real page
        elif url == "https://www.cdiscount.com/":
            text = _HOME_HTML
        elif "/l-" in url:
            text = _LIST_HTML
        else:
            text = _PDP_HTML
        return FetchResult(
            ok=True,
            url=url,
            status=200,
            text=text,
            content=text.encode(),
            final_url=url,
            fetcher="curl_cffi",
        )

    def fake_post(url: str, **kw) -> FetchResult:
        nonlocal post_call_count
        post_call_count += 1
        calls.append(("POST", url))
        crawler.counter.api_calls += 1
        return FetchResult(
            ok=True,
            url=url,
            status=200,
            text="",
            content=b"",
            final_url=url,
            fetcher="curl_cffi",
        )

    class _FakeFetcher:
        def get(self, url, **kw):
            return fake_get(url, **kw)

        def post(self, url, **kw):
            return fake_post(url, **kw)

    monkeypatch.setattr(crawler, "make_fetcher", lambda **kw: _FakeFetcher())

    result = crawler.crawl()

    # Must have made the Baleen POST (challenge check)
    assert post_call_count >= 1, (
        f"Expected >=1 POST call for Baleen handshake, got {post_call_count}. "
        f"All calls: {calls}"
    )
    # At least: 2x home GET + 1 POST + 1+ list GET + 1 PDP GET
    assert crawler.counter.api_calls >= 4, (
        f"Expected >=4 api_calls, got {crawler.counter.api_calls}. "
        f"Calls: {calls}"
    )
    # Must have parsed at least one product
    assert isinstance(result.products, list)
    assert len(result.products) >= 1, (
        f"Expected >=1 product, got {result.products}. Notes: {result.notes}"
    )
    product = result.products[0]
    assert product["sku"] == _SKU
    assert product["title"] == "Samsung TV 55 pouces UHD"
    assert product["sale_price"] == 649.99
    assert product["currency"] == "EUR"
    assert product["site"] == "cdiscount"
    assert product["brand"] == "Samsung"


def test_cdiscount_product_parsing_fields(monkeypatch):
    """Verify _parse_product extracts all expected fields from the fixture HTML."""
    from app.crawlers.cdiscount import CdiscountCrawler

    crawler = CdiscountCrawler(_site())
    row = crawler._parse_product(_PDP_HTML, _PROD_URL)

    assert row is not None, "Expected a product dict, got None"
    assert row["sku"] == _SKU
    assert row["spu"] == _SKU
    assert row["title"] == "Samsung TV 55 pouces UHD"
    assert row["sale_price"] == 649.99
    assert row["original_price"] == 649.99
    assert row["currency"] == "EUR"
    assert row["status"] == "on_sale"
    assert row["ratings"] == 4.5
    assert row["review_count"] == 230
    assert row["brand"] == "Samsung"
    assert "High-Tech" in (row["category_path"] or "")
    assert row["product_url"] == _PROD_URL
    assert row["site"] == "cdiscount"


def test_cdiscount_counter_increments_minimum(monkeypatch):
    """Weaker smoke: at minimum one api_call recorded (proves unified path)."""
    from app.crawlers.cdiscount import CdiscountCrawler

    crawler = CdiscountCrawler(_site())
    crawler.limit = 1

    get_count = 0

    def fake_get(url: str, **kw) -> FetchResult:
        nonlocal get_count
        get_count += 1
        crawler.counter.api_calls += 1
        if url == "https://www.cdiscount.com/" and get_count == 1:
            text = _BALEEN_STUB_HTML
        elif url == "https://www.cdiscount.com/":
            text = _HOME_HTML
        elif "/l-" in url:
            text = _LIST_HTML
        else:
            text = _PDP_HTML
        return FetchResult(
            ok=True, url=url, status=200, text=text,
            content=text.encode(), final_url=url, fetcher="curl_cffi",
        )

    class _F:
        def get(self, url, **kw):
            return fake_get(url, **kw)

        def post(self, url, **kw):
            crawler.counter.api_calls += 1
            return FetchResult(
                ok=True, url=url, status=200, text="",
                content=b"", final_url=url, fetcher="curl_cffi",
            )

    monkeypatch.setattr(crawler, "make_fetcher", lambda **kw: _F())
    crawler.crawl()
    assert crawler.counter.api_calls >= 1
