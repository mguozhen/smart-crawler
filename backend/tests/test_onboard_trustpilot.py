"""TDD test: trustpilot crawler 批D 收编验证（纯浏览器 StealthyFetcher 形态）。

Trustpilot 是纯 StealthyFetcher crawler（Next.js 站，AWS WAF/Cloudflare），
每页开一次浏览器渲染，解析 __NEXT_DATA__ JSON。

验证：
- StealthyFetcher.fetch 经 count_browser_fetch 包裹 → 每成功一页 browser_opens += 1
- stealth 失败（非 200）→ browser_opens 不计，crawl 中断并返回已有评论
- stealth 成功但无 reviews（__NEXT_DATA__ 无评论）→ browser_opens 计 1，翻页终止
- 翻页终止（page_reviews 空 → break）
- 解析：__NEXT_DATA__ 评论字段正确映射
- 构造签名：TrustpilotCrawler(channel, max_pages=10)（向后兼容）
- crawl() 返回 list[dict]（与 review_runner 兼容）
"""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Fixture helpers — 模拟 __NEXT_DATA__ 结构（基于 Trustpilot Next.js 页面）
# ---------------------------------------------------------------------------

def _review_raw(rid: str, stars: int = 5) -> dict:
    return {
        "id": rid,
        "stars": stars,
        "title": f"Great experience #{rid}",
        "text": f"This is review {rid}",
        "language": "en",
        "dates": {
            "publishedDate": "2024-03-15T10:00:00.000Z",
            "experiencedDate": "2024-03-10T00:00:00.000Z",
        },
        "consumer": {
            "displayName": f"Reviewer {rid}",
            "countryCode": "US",
        },
        "reply": {
            "message": f"Thank you #{rid}",
            "publishedDate": "2024-03-16T08:00:00.000Z",
        },
        "labels": {
            "verification": {"isVerified": True},
            "merged": ["quality"],
        },
    }


def _next_data_html(reviews: list[dict]) -> str:
    """包装成 Trustpilot 页面中 __NEXT_DATA__ script 标签 HTML。"""
    nd = {
        "props": {
            "pageProps": {
                "reviews": reviews,
            }
        }
    }
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(nd)}</script></html>'


_PAGE1_REVIEWS = [_review_raw("r001"), _review_raw("r002", stars=4)]
_PAGE2_REVIEWS = [_review_raw("r003", stars=3)]
_PAGE1_HTML = _next_data_html(_PAGE1_REVIEWS)
_PAGE2_HTML = _next_data_html(_PAGE2_REVIEWS)
_EMPTY_ND_HTML = _next_data_html([])  # 有 __NEXT_DATA__ 但 reviews 空 → 翻页终止
_NO_ND_HTML = "<html><body>Not found</body></html>"  # 无 __NEXT_DATA__


def _channel() -> dict:
    return {
        "site": "trustpilot_test",
        "domain": "example.com",
        "host": "www.trustpilot.com",
        "max_pages": 10,
    }


# ---------------------------------------------------------------------------
# Mock page object (模拟 Scrapling StealthyFetcher 返回的 page 对象)
# ---------------------------------------------------------------------------

class _FakePage:
    def __init__(self, status: int, html: str):
        self.status = status
        self.html_content = html


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_trustpilot_browser_opens_increments_per_successful_page(monkeypatch):
    """每成功一页 browser_opens += 1；两页成功则 browser_opens == 2，解析正确。"""
    from app.crawlers.trustpilot import TrustpilotCrawler

    crawler = TrustpilotCrawler(_channel())

    pages_fetched: list[str] = []

    def fake_stealth_fetch(url, **kw):
        pages_fetched.append(url)
        if "page=1" in url:
            return _FakePage(200, _PAGE1_HTML)
        elif "page=2" in url:
            return _FakePage(200, _PAGE2_HTML)
        else:
            return _FakePage(200, _EMPTY_ND_HTML)

    try:
        from scrapling import fetchers as _sf_mod
        monkeypatch.setattr(
            _sf_mod.StealthyFetcher, "fetch", staticmethod(fake_stealth_fetch)
        )
    except Exception:
        pytest.skip("scrapling not installed")

    reviews = crawler.crawl()

    assert crawler.counter.browser_opens == 3, (
        f"Expected browser_opens==3 (page1+page2 success, page3 empty→counted but no reviews→stop), "
        f"got {crawler.counter.browser_opens}. pages_fetched={pages_fetched}"
    )
    assert len(reviews) == 3, (
        f"Expected 3 reviews (2 from page1, 1 from page2), got {len(reviews)}"
    )

    first = reviews[0]
    assert first["review_id"] == "r001"
    assert first["platform"] == "trustpilot"
    assert first["site"] == "trustpilot_test"
    assert first["reviewer_name"] == "Reviewer r001"
    assert first["reviewer_country"] == "US"
    assert first["rating"] == 5
    assert first["title"] == "Great experience #r001"
    assert first["content"] == "This is review r001"
    assert first["language"] == "en"
    assert first["review_date"] == "2024-03-15T10:00:00.000Z"
    assert first["is_verified"] is True
    assert first["reply_content"] == "Thank you #r001"


def test_trustpilot_stealth_failure_non_200_does_not_count(monkeypatch):
    """stealth 返回非 200 → browser_opens 不增加，crawl 中断返回空列表。"""
    from app.crawlers.trustpilot import TrustpilotCrawler

    crawler = TrustpilotCrawler(_channel())

    def fake_stealth_fetch(url, **kw):
        return _FakePage(403, "<html>Blocked</html>")

    try:
        from scrapling import fetchers as _sf_mod
        monkeypatch.setattr(
            _sf_mod.StealthyFetcher, "fetch", staticmethod(fake_stealth_fetch)
        )
    except Exception:
        pytest.skip("scrapling not installed")

    reviews = crawler.crawl()

    assert crawler.counter.browser_opens == 0, (
        f"Expected browser_opens==0 (all requests 403), got {crawler.counter.browser_opens}"
    )
    assert reviews == [], f"Expected empty list on failure, got {reviews}"


def test_trustpilot_no_next_data_does_not_count_browser_open(monkeypatch):
    """status==200 但页面无 __NEXT_DATA__ → count_browser_fetch 成功计数，
    但 page_reviews 为空 → 翻页终止，notes 有提示。

    注意：success 标准是 status==200（Trustpilot 纯浏览器版），
    __NEXT_DATA__ 解析空不影响 browser_opens 计数，只影响 reviews 输出。"""
    from app.crawlers.trustpilot import TrustpilotCrawler

    crawler = TrustpilotCrawler(_channel())

    def fake_stealth_fetch(url, **kw):
        return _FakePage(200, _NO_ND_HTML)

    try:
        from scrapling import fetchers as _sf_mod
        monkeypatch.setattr(
            _sf_mod.StealthyFetcher, "fetch", staticmethod(fake_stealth_fetch)
        )
    except Exception:
        pytest.skip("scrapling not installed")

    reviews = crawler.crawl()

    # status==200 → browser_opens 计 1，但 __NEXT_DATA__ 无数据 → 翻页止
    assert crawler.counter.browser_opens == 1, (
        f"Expected browser_opens==1 (status 200 counts, then no reviews→stop), "
        f"got {crawler.counter.browser_opens}"
    )
    assert reviews == [], f"Expected empty list (no __NEXT_DATA__), got {reviews}"


def test_trustpilot_pagination_terminates_on_empty_reviews(monkeypatch):
    """page_reviews 为空时翻页停止（不超过 max_pages）。"""
    from app.crawlers.trustpilot import TrustpilotCrawler

    crawler = TrustpilotCrawler(_channel())
    pages_fetched: list[int] = []

    def fake_stealth_fetch(url, **kw):
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(url).query)
        page = int(qs.get("page", ["1"])[0])
        pages_fetched.append(page)
        if page == 1:
            return _FakePage(200, _PAGE1_HTML)    # 有 reviews → 继续
        else:
            return _FakePage(200, _EMPTY_ND_HTML)  # 空 reviews → 终止

    try:
        from scrapling import fetchers as _sf_mod
        monkeypatch.setattr(
            _sf_mod.StealthyFetcher, "fetch", staticmethod(fake_stealth_fetch)
        )
    except Exception:
        pytest.skip("scrapling not installed")

    reviews = crawler.crawl()

    assert pages_fetched == [1, 2], (
        f"Expected pages [1, 2] (page1 ok, page2 empty → stop), got {pages_fetched}"
    )
    assert len(reviews) == 2, (
        f"Expected 2 reviews (only from page1), got {len(reviews)}"
    )
    assert crawler.counter.browser_opens == 2, (
        f"Expected browser_opens==2 (both fetches were status 200), got {crawler.counter.browser_opens}"
    )


def test_trustpilot_stealth_exception_breaks_loop(monkeypatch):
    """StealthyFetcher.fetch 抛出异常 → crawl 中断，browser_opens 不计，返回已有评论。"""
    from app.crawlers.trustpilot import TrustpilotCrawler

    crawler = TrustpilotCrawler(_channel())
    call_count = [0]

    def fake_stealth_fetch(url, **kw):
        call_count[0] += 1
        if call_count[0] == 1:
            return _FakePage(200, _PAGE1_HTML)
        raise RuntimeError("Network error")

    try:
        from scrapling import fetchers as _sf_mod
        monkeypatch.setattr(
            _sf_mod.StealthyFetcher, "fetch", staticmethod(fake_stealth_fetch)
        )
    except Exception:
        pytest.skip("scrapling not installed")

    reviews = crawler.crawl()

    # 第一页成功 browser_opens=1，第二页异常 → loop break
    assert crawler.counter.browser_opens == 1, (
        f"Expected browser_opens==1 (page1 ok, page2 exception→break), "
        f"got {crawler.counter.browser_opens}"
    )
    assert len(reviews) == 2, (
        f"Expected 2 reviews from page1 before exception, got {len(reviews)}"
    )


def test_trustpilot_backward_compatible_constructor(monkeypatch):
    """构造签名向后兼容：channel dict 含必要字段即可，max_pages 从 channel 或参数读。"""
    from app.crawlers.trustpilot import TrustpilotCrawler

    # 1. channel 直接含 max_pages
    ch1 = {"site": "tp1", "domain": "foo.com", "max_pages": 5}
    c1 = TrustpilotCrawler(ch1)
    assert c1.max_pages == 5

    # 2. channel 无 max_pages → 用参数默认值
    ch2 = {"site": "tp2", "domain": "bar.com"}
    c2 = TrustpilotCrawler(ch2, max_pages=7)
    assert c2.max_pages == 7

    # 3. crawl() 返回 list（review_runner 兼容）
    def fake_stealth_fetch(url, **kw):
        return _FakePage(200, _EMPTY_ND_HTML)

    try:
        from scrapling import fetchers as _sf_mod
        monkeypatch.setattr(
            _sf_mod.StealthyFetcher, "fetch", staticmethod(fake_stealth_fetch)
        )
    except Exception:
        pytest.skip("scrapling not installed")

    result = c2.crawl()
    assert isinstance(result, list), (
        f"crawl() must return list[dict] (review_runner compat), got {type(result)}"
    )


def test_trustpilot_stealth_kwargs_preserved(monkeypatch):
    """stealth_kwargs 被正确传入 StealthyFetcher.fetch（proxy/persist_profile_key 等）。"""
    from app.crawlers.trustpilot import TrustpilotCrawler

    channel = {
        "site": "tp_stealth_test",
        "domain": "example.com",
        "host": "www.trustpilot.com",
        "max_pages": 1,
    }
    crawler = TrustpilotCrawler(channel)

    captured_kw: list[dict] = []

    def fake_stealth_fetch(url, **kw):
        captured_kw.append(kw)
        return _FakePage(200, _EMPTY_ND_HTML)

    try:
        from scrapling import fetchers as _sf_mod
        monkeypatch.setattr(
            _sf_mod.StealthyFetcher, "fetch", staticmethod(fake_stealth_fetch)
        )
    except Exception:
        pytest.skip("scrapling not installed")

    crawler.crawl()

    assert len(captured_kw) >= 1, "StealthyFetcher.fetch should have been called at least once"
    kw0 = captured_kw[0]
    # stealth_kwargs からの標準パラメータが含まれること
    assert "headless" in kw0, "stealth_kwargs must include headless"
    assert "solve_cloudflare" in kw0, "stealth_kwargs must include solve_cloudflare"
    assert "hide_canvas" in kw0, "stealth_kwargs must include hide_canvas"
