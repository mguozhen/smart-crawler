"""TDD test: verify etsy crawler routes through BaseCrawler.make_fetcher
and increments counter.api_calls via the unified fetch layer.

Before migration: EtsyCrawler uses raw curl_cffi Session directly; make_fetcher
is never called; counter.api_calls stays 0.
After migration: every HTTP GET goes through the unified CrawlerFetcher;
counter.api_calls is incremented on each successful fetch.
"""
from __future__ import annotations

import json

import pytest

from app.fetching import FetchResult
from app.models import Site

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Minimal fixture HTML helpers
# ---------------------------------------------------------------------------

_LISTING_ID = "123456789"

# SRP page: must contain at least one URL matching _LISTING_RE
# Pattern: /listing/(\d{6,12})/[a-zA-Z0-9_-]+
_SRP_HTML = (
    "<html><body>"
    f'<a href="/listing/{_LISTING_ID}/handmade-mug">Product</a>'
    # Pad to > 20000 chars so _blocked() size check passes
    + " " * 25000
    + "</body></html>"
)

# PDP page: must contain a JSON-LD Product block for _parse_jsonld to parse
_PRODUCT_JSONLD = {
    "@context": "https://schema.org",
    "@type": "Product",
    "name": "Handmade Ceramic Mug",
    "description": "A beautiful handmade mug.",
    "image": ["https://i.etsystatic.com/example.jpg"],
    "brand": {"@type": "Brand", "name": "TestShop"},
    "offers": {
        "@type": "Offer",
        "price": "25.00",
        "priceCurrency": "USD",
        "availability": "https://schema.org/InStock",
    },
    "aggregateRating": {
        "@type": "AggregateRating",
        "ratingValue": "4.8",
        "reviewCount": "120",
    },
}

_PDP_HTML = (
    "<html><head>"
    '<script type="application/ld+json">'
    + json.dumps(_PRODUCT_JSONLD)
    + "</script>"
    + "</head><body>"
    + " " * 25000
    + "</body></html>"
)

_PDP_URL = f"https://www.etsy.com/listing/{_LISTING_ID}/handmade-mug"


def _site() -> Site:
    s = Site()
    s.site = "etsy"
    s.url = "https://www.etsy.com"
    s.country = "US"
    s.proxy_tier = "none"
    s.platform = "etsy"
    s.brand = None
    return s


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_etsy_routes_through_make_fetcher_and_counts(monkeypatch):
    """After migration, all HTTP GETs go through make_fetcher → counter increments."""
    from app.crawlers.etsy import EtsyCrawler

    crawler = EtsyCrawler(_site(), limit=1)

    # Track calls per URL type
    calls: list[str] = []

    def fake_get(url: str, **kw) -> FetchResult:
        calls.append(url)
        crawler.counter.api_calls += 1
        # SRP urls contain /search?
        if "/search?" in url:
            html = _SRP_HTML
        else:
            html = _PDP_HTML
        return FetchResult(
            ok=True,
            url=url,
            status=200,
            text=html,
            content=html.encode(),
            final_url=url,
            fetcher="curl_cffi",
        )

    class _FakeFetcher:
        def get(self, url, **kw):
            return fake_get(url, **kw)

    # Patch make_fetcher to return our fake fetcher
    monkeypatch.setattr(crawler, "make_fetcher", lambda **kw: _FakeFetcher())

    result = crawler.crawl()

    # At least one SRP call + one PDP call must have gone through make_fetcher
    assert crawler.counter.api_calls >= 2, (
        f"Expected >=2 api_calls (1 SRP + 1 PDP), got {crawler.counter.api_calls}. "
        f"URLs fetched: {calls}"
    )
    # Must have parsed at least one product
    assert isinstance(result.products, list)
    assert len(result.products) >= 1, (
        f"Expected >=1 product parsed, got {result.products}. Notes: {result.notes}"
    )
    # Verify the parsed product has expected fields
    product = result.products[0]
    assert product["sku"] == _LISTING_ID
    assert product["title"] == "Handmade Ceramic Mug"
    assert product["sale_price"] == 25.0
    assert product["currency"] == "USD"
    assert product["site"] == "etsy"


def test_etsy_counter_api_calls_minimum(monkeypatch):
    """Weaker smoke: at minimum one api_call is recorded (proves unified path)."""
    from app.crawlers.etsy import EtsyCrawler

    crawler = EtsyCrawler(_site(), limit=1)

    def fake_get(url: str, **kw) -> FetchResult:
        crawler.counter.api_calls += 1
        if "/search?" in url:
            html = _SRP_HTML
        else:
            html = _PDP_HTML
        return FetchResult(
            ok=True, url=url, status=200, text=html,
            content=html.encode(), final_url=url, fetcher="curl_cffi",
        )

    class _F:
        def get(self, url, **kw):
            return fake_get(url, **kw)

    monkeypatch.setattr(crawler, "make_fetcher", lambda **kw: _F())
    crawler.crawl()
    assert crawler.counter.api_calls >= 1
