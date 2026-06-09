from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Product, Review
from app.ondemand.base import BaseOnDemand, OnDemandResult

pytestmark = pytest.mark.unit


class FakeCrawler(BaseOnDemand):
    platform = "fake"
    proxy_tier = "none"

    @staticmethod
    def parse_item_id(url):
        return "IT1"

    @staticmethod
    def parse_listing(data, url):
        return {"sku": "IT1", "title": "Fake Chair", "site": "ondemand_fake",
                "product_url": url, "sale_price": 10.0}

    @staticmethod
    def parse_reviews(data, item_id, url):
        return [{"review_id": "rv1", "platform": "ondemand_fake",
                 "site": "ondemand_fake", "rating": 5, "content": "ok"}]

    def fetch_listing(self, item_id, url, proxy=None):
        return self.parse_listing({}, url)

    def fetch_reviews(self, item_id, url, limit=100, proxy=None):
        return self.parse_reviews({}, item_id, url)

    def enumerate_listing(self, url, max_items=100, proxy=None):
        return ["IT1", "IT2"]


def test_fetch_single_product_collects_listing_and_reviews():
    from app.ondemand.runner import fetch

    res = fetch("https://x/IT1", crawler=FakeCrawler(), kind="product",
                do_persist=False)
    assert isinstance(res, OnDemandResult)
    assert len(res.listings) == 1
    assert res.listings[0]["sku"] == "IT1"
    assert len(res.reviews) == 1


def test_fetch_listing_enumerates_multiple(monkeypatch):
    from app.ondemand.runner import fetch

    # listing 路径走 enumerate_listing(["IT1","IT2"]),fetch_listing 固定返回 sku=IT1
    res = fetch("https://x/shop", crawler=FakeCrawler(), kind="listing",
                max_items=2, do_persist=False)
    assert len(res.listings) == 2


def test_proxy_tunnel_error_rotates_and_reports(monkeypatch):
    """坏代理(net::ERR_TUNNEL_CONNECTION_FAILED)应换出口重试到好代理并成功,
    且坏出口要被上报失败(踢进冷却),好出口被上报成功。

    复现线上 bug:tunnel 失败是普通 Exception,旧 runner 直接放弃、不换 IP、
    也从不调用 report_failure → 命中坏出口就连续失败。"""
    from app import proxy_pool
    from app.ondemand import runner

    # 代理池:第一次给坏出口,之后给好出口
    proxies = iter(["http://u:p@bad:1", "http://u:p@good:2", "http://u:p@good:2"])
    monkeypatch.setattr(runner, "get_proxy", lambda tier: next(proxies))

    failed, succeeded = [], []
    monkeypatch.setattr(proxy_pool, "report_failure",
                        lambda url, **kw: failed.append(url))
    monkeypatch.setattr(proxy_pool, "report_success",
                        lambda url: succeeded.append(url))

    class FlakyCrawler(FakeCrawler):
        def fetch_listing(self, item_id, url, proxy=None):
            if "bad" in (proxy or ""):
                raise RuntimeError(
                    "Page.goto: net::ERR_TUNNEL_CONNECTION_FAILED at https://x")
            return self.parse_listing({}, url)

    res = runner.fetch("https://x/IT1", crawler=FlakyCrawler(),
                       kind="product", do_persist=False)

    assert len(res.listings) == 1                 # 换到好出口后成功
    assert "http://u:p@bad:1" in failed           # 坏出口被踢进冷却
    assert "http://u:p@good:2" in succeeded        # 好出口被记成功


def test_listing_blocked_still_collects_reviews(monkeypatch):
    """listing 全程被封时,评论仍应单独抓到(评论接口反爬宽松、独立去重)。

    复现本地无住宅代理场景:listing 渲染必被弹验证页,但评论 HTTP 接口可达——
    不该因 listing 失败把评论一起丢掉。"""
    from app.antiban import BlockedError
    from app.ondemand import runner

    monkeypatch.setattr(runner, "get_proxy", lambda tier: None)

    class ListingBlockedCrawler(FakeCrawler):
        def fetch_listing(self, item_id, url, proxy=None):
            raise BlockedError("pdp 被弹账号验证页")

    res = runner.fetch("https://x/IT1", crawler=ListingBlockedCrawler(),
                       kind="product", do_persist=False)

    assert len(res.listings) == 0                 # listing 没拿到
    assert len(res.reviews) == 1                   # 评论仍单独抓到
    assert any("listing 多次被封" in n for n in res.notes)


def test_review_failure_does_not_drop_listing(monkeypatch):
    """评论抓取报错应被隔离,不影响已拿到的 listing。"""
    from app.ondemand import runner

    monkeypatch.setattr(runner, "get_proxy", lambda tier: None)

    class ReviewBrokenCrawler(FakeCrawler):
        def fetch_reviews(self, item_id, url, limit=100, proxy=None):
            raise RuntimeError("评论接口 500")

    res = runner.fetch("https://x/IT1", crawler=ReviewBrokenCrawler(),
                       kind="product", do_persist=False)

    assert len(res.listings) == 1                  # listing 不受评论失败影响
    assert len(res.reviews) == 0
    assert any("评论抓取失败" in n for n in res.notes)


def test_persist_writes_product_and_review():
    from app.ondemand import runner

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    res = OnDemandResult()
    res.add_listing({"sku": "IT1", "title": "Fake Chair", "site": "ondemand_fake",
                     "product_url": "https://x/IT1", "sale_price": 10.0})
    res.add_reviews([{"review_id": "rv1", "platform": "ondemand_fake",
                      "site": "ondemand_fake", "rating": 5, "content": "ok"}])

    sess = TestSession()
    stats = runner.persist(res, session=sess)
    sess.commit()

    assert sess.query(Product).filter_by(sku="IT1").count() == 1
    assert sess.query(Review).filter_by(review_id="rv1").count() == 1
    assert stats["listings"]["inserted"] == 1
    assert stats["reviews"]["inserted"] == 1
    sess.close()
