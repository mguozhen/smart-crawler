"""TDD test: verify google_maps crawler routes through BaseCrawler.count_browser_fetch
and increments counter.browser_opens exactly once per crawl call, regardless of how
many scroll iterations happen inside the page_action callback.

Before migration: GoogleMapsCrawler does not inherit BaseCrawler; counter never
  increments; browser_opens stays 0.
After migration: the single StealthyFetcher.fetch() call is wrapped in
  count_browser_fetch → counter.browser_opens == 1 (scroll loops do NOT add more).
"""
from __future__ import annotations

import re
import types

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _channel(*, max_reviews: int = 16) -> dict:
    return {
        "platform": "google_map",
        "query": "Acme Store",
        "site": "google_maps_test",
        "max_reviews": max_reviews,
    }


def _fake_card(rid: str, name: str = "Alice", stars: int = 5,
               text: str = "Great place!") -> object:
    """Minimal fake card that mimics Scrapling element interface."""
    class _Node:
        def __init__(self, t):
            self._t = t

        @property
        def text(self):
            return self._t

    class _Img:
        attrib = {"aria-label": f"{stars} stars"}

    class _Card:
        attrib = {"data-review-id": rid}

        def css_first(self, selector):
            if "wiI7pd" in selector or "MyEned" in selector:
                return _Node(text)
            if "d4r55" in selector or "TSUbDb" in selector:
                return _Node(name)
            if "img" in selector or "star" in selector or "星" in selector:
                return _Img()
            return None

    return _Card()


def _fake_page(cards=None, status: int = 200) -> object:
    """Fake Scrapling FetchedPage-like object with .css() and .status."""
    class _FakePage:
        def __init__(self):
            self.status = status
            self._cards = cards or []

        def css(self, selector):
            if "data-review-id" in selector:
                return self._cards
            return []

    return _FakePage()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_google_maps_browser_opens_exactly_once(monkeypatch):
    """After migration: one crawl → browser_opens == 1, even with scroll loops."""
    from app.crawlers.google_maps import GoogleMapsCrawler

    channel = _channel(max_reviews=16)  # scroll loop runs min(16//8, 30)=2 iters
    crawler = GoogleMapsCrawler(channel)

    cards = [_fake_card(f"rev-{i}", stars=4 + i % 2) for i in range(5)]
    fake_page = _fake_page(cards=cards, status=200)

    fetch_call_count = [0]

    def fake_fetch(url, **kw):
        fetch_call_count[0] += 1
        # Simulate page_action running (scroll), but it doesn't call fetch again
        page_action = kw.get("page_action")
        if page_action:
            # page_action receives a playwright page; just call it with fake page
            page_action(fake_page)
        return fake_page

    # Patch StealthyFetcher.fetch at module level used by google_maps
    import app.crawlers.google_maps as gm_module

    # We need to patch where StealthyFetcher is imported (inside crawl())
    scrapling_fetchers = types.ModuleType("scrapling.fetchers")
    scrapling_fetchers.StealthyFetcher = type(
        "StealthyFetcher", (), {"fetch": staticmethod(fake_fetch)}
    )
    scrapling_mod = types.ModuleType("scrapling")

    monkeypatch.setitem(__import__("sys").modules, "scrapling", scrapling_mod)
    monkeypatch.setitem(__import__("sys").modules, "scrapling.fetchers", scrapling_fetchers)

    # Also patch _stealth_config.stealth_kwargs to return minimal dict
    monkeypatch.setattr(
        "app.crawlers._stealth_config.stealth_kwargs",
        lambda **kw: {"page_action": kw.get("extra", {}).get("page_action")},
    )

    reviews = crawler.crawl()

    # Core assertion: exactly one browser_open counted
    assert crawler.counter.browser_opens == 1, (
        f"Expected browser_opens == 1 (one StealthyFetcher.fetch call = one browser open), "
        f"got {crawler.counter.browser_opens}. "
        f"fetch_call_count={fetch_call_count[0]}, notes={crawler.notes}"
    )

    # fetch was only called once
    assert fetch_call_count[0] == 1, (
        f"StealthyFetcher.fetch should be called exactly once, got {fetch_call_count[0]}"
    )

    # Reviews were parsed correctly
    assert isinstance(reviews, list)
    assert len(reviews) == 5, (
        f"Expected 5 reviews from 5 cards, got {len(reviews)}. Notes: {crawler.notes}"
    )

    first = reviews[0]
    assert first["platform"] == "google_map"
    assert first["site"] == "google_maps_test"
    assert "review_id" in first
    assert first["review_id"] == "rev-0"


def test_google_maps_failure_does_not_increment_browser_opens(monkeypatch):
    """Stealth failure (HTTP 403 or exception) must NOT increment browser_opens."""
    from app.crawlers.google_maps import GoogleMapsCrawler

    channel = _channel()
    crawler = GoogleMapsCrawler(channel)

    # Scenario A: HTTP 403 response (success=False → no increment)
    fake_403 = _fake_page(cards=[], status=403)

    def fake_fetch_403(url, **kw):
        return fake_403

    import sys
    scrapling_fetchers = types.ModuleType("scrapling.fetchers")
    scrapling_fetchers.StealthyFetcher = type(
        "StealthyFetcher", (), {"fetch": staticmethod(fake_fetch_403)}
    )
    scrapling_mod = types.ModuleType("scrapling")
    monkeypatch.setitem(sys.modules, "scrapling", scrapling_mod)
    monkeypatch.setitem(sys.modules, "scrapling.fetchers", scrapling_fetchers)

    monkeypatch.setattr(
        "app.crawlers._stealth_config.stealth_kwargs",
        lambda **kw: {"page_action": kw.get("extra", {}).get("page_action")},
    )

    reviews_403 = crawler.crawl()

    assert crawler.counter.browser_opens == 0, (
        f"HTTP 403 should not increment browser_opens, "
        f"got {crawler.counter.browser_opens}"
    )
    assert reviews_403 == []


def test_google_maps_exception_does_not_increment_browser_opens(monkeypatch):
    """Exception during fetch must not increment browser_opens (exception re-propagated or caught)."""
    from app.crawlers.google_maps import GoogleMapsCrawler

    channel = _channel()
    crawler = GoogleMapsCrawler(channel)

    def fake_fetch_exc(url, **kw):
        raise RuntimeError("connection refused")

    import sys
    scrapling_fetchers = types.ModuleType("scrapling.fetchers")
    scrapling_fetchers.StealthyFetcher = type(
        "StealthyFetcher", (), {"fetch": staticmethod(fake_fetch_exc)}
    )
    scrapling_mod = types.ModuleType("scrapling")
    monkeypatch.setitem(sys.modules, "scrapling", scrapling_mod)
    monkeypatch.setitem(sys.modules, "scrapling.fetchers", scrapling_fetchers)

    monkeypatch.setattr(
        "app.crawlers._stealth_config.stealth_kwargs",
        lambda **kw: {"page_action": kw.get("extra", {}).get("page_action")},
    )

    # Exception is caught inside crawl() → returns []
    reviews = crawler.crawl()

    assert crawler.counter.browser_opens == 0, (
        f"Exception during fetch should not increment browser_opens, "
        f"got {crawler.counter.browser_opens}"
    )
    assert reviews == []


def test_google_maps_max_reviews_respected(monkeypatch):
    """max_reviews cap is applied to parsed output."""
    from app.crawlers.google_maps import GoogleMapsCrawler

    channel = _channel(max_reviews=3)
    crawler = GoogleMapsCrawler(channel)

    cards = [_fake_card(f"r{i}") for i in range(10)]
    fake_page = _fake_page(cards=cards, status=200)

    def fake_fetch(url, **kw):
        return fake_page

    import sys
    scrapling_fetchers = types.ModuleType("scrapling.fetchers")
    scrapling_fetchers.StealthyFetcher = type(
        "StealthyFetcher", (), {"fetch": staticmethod(fake_fetch)}
    )
    scrapling_mod = types.ModuleType("scrapling")
    monkeypatch.setitem(sys.modules, "scrapling", scrapling_mod)
    monkeypatch.setitem(sys.modules, "scrapling.fetchers", scrapling_fetchers)

    monkeypatch.setattr(
        "app.crawlers._stealth_config.stealth_kwargs",
        lambda **kw: {"page_action": kw.get("extra", {}).get("page_action")},
    )

    reviews = crawler.crawl()

    assert len(reviews) <= 3, (
        f"max_reviews=3 cap should truncate to <=3, got {len(reviews)}"
    )
    assert crawler.counter.browser_opens == 1
