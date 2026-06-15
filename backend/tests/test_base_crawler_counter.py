from __future__ import annotations

import pytest

from app.crawlers.base import BaseCrawler, CrawlResult
from app.fetching import CrawlCounter
from app.models import Site

pytestmark = pytest.mark.unit


class _Dummy(BaseCrawler):
    platform = "generic"

    def crawl(self) -> CrawlResult:
        return CrawlResult()


def _site():
    return Site(site="t", url="https://example.com", country="US",
                proxy_tier="none", platform="generic")


def test_crawlresult_has_count_fields():
    r = CrawlResult()
    assert r.api_calls == 0
    assert r.browser_opens == 0
    assert r.pages_fetched == 0


def test_base_crawler_has_counter():
    c = _Dummy(_site())
    assert isinstance(c.counter, CrawlCounter)
    assert c.counter.pages_fetched == 0


def test_make_fetcher_injects_counter():
    c = _Dummy(_site())
    c.counter.api_calls = 2
    fetcher = c.make_fetcher(kind="product", source="test")
    assert fetcher.context.counter is c.counter
