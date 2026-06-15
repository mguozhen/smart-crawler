from __future__ import annotations

import pytest

from app.crawlers.base import BaseCrawler, CrawlResult
from app.models import Site

pytestmark = pytest.mark.unit


class _Dummy(BaseCrawler):
    platform = "generic"

    def crawl(self) -> CrawlResult:
        return CrawlResult()


def _site():
    return Site(site="t", url="https://example.com", country="US",
                proxy_tier="none", platform="generic")


def test_count_browser_fetch_success_increments():
    c = _Dummy(_site())
    out = c.count_browser_fetch(lambda: "<html>ok</html>")
    assert out == "<html>ok</html>"
    assert c.counter.browser_opens == 1
    assert c.counter.api_calls == 0


def test_count_browser_fetch_falsy_does_not_count():
    c = _Dummy(_site())
    c.count_browser_fetch(lambda: None)
    assert c.counter.browser_opens == 0


def test_count_browser_fetch_custom_success():
    c = _Dummy(_site())
    c.count_browser_fetch(lambda: "short", success=lambda r: len(r) > 10)
    assert c.counter.browser_opens == 0
    c.count_browser_fetch(lambda: "a long enough html body",
                          success=lambda r: len(r) > 10)
    assert c.counter.browser_opens == 1


def test_count_api_fetch_success_increments():
    c = _Dummy(_site())
    out = c.count_api_fetch(lambda: {"data": 1})
    assert out == {"data": 1}
    assert c.counter.api_calls == 1
    assert c.counter.browser_opens == 0


def test_count_fetch_propagates_exception():
    c = _Dummy(_site())
    def boom():
        raise RuntimeError("fetch failed")
    with pytest.raises(RuntimeError):
        c.count_browser_fetch(boom)
    assert c.counter.browser_opens == 0
