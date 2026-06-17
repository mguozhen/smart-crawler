from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.crawl_diagnostics import FailureInfo, STAGE_JOB, record_failure
from app.crawlers.base import CrawlResult
from app.db import SessionLocal, init_db
from app.models import CrawlFailure, CrawlJob, Product, Site
from app.runner import claim_job, enqueue, execute_job

pytestmark = pytest.mark.unit


class _ZeroCrawler:
    job_id: int | None = None

    def crawl(self) -> CrawlResult:
        s = SessionLocal()
        try:
            record_failure(
                s,
                site="runner_zero_probe",
                job_id=self.job_id,
                info=FailureInfo(
                    "network_timeout",
                    STAGE_JOB,
                    "sitemap timeout",
                    True,
                    "检查代理后重跑",
                ),
            )
            s.commit()
        finally:
            s.close()
        out = CrawlResult()
        out.notes.append("sitemap timeout")
        return out


class _PartialCrawler:
    job_id: int | None = None

    def crawl(self) -> CrawlResult:
        s = SessionLocal()
        try:
            record_failure(
                s,
                site="runner_partial_probe",
                job_id=self.job_id,
                info=FailureInfo(
                    "http_429",
                    "fetch",
                    "rate limited after first page",
                    True,
                    "降低并发和频率，延长冷却时间或更换代理出口",
                ),
            )
            s.commit()
        finally:
            s.close()
        out = CrawlResult()
        out.products.append({
            "sku": "PARTIAL-1",
            "title": "Partial Product",
            "site": "runner_partial_probe",
            "product_url": "https://example.com/products/partial-1",
            "sale_price": 10,
        })
        out.notes.append("rate limited after first page")
        return out


def test_execute_job_zero_products_preserves_specific_failure(monkeypatch):
    init_db()
    s = SessionLocal()
    try:
        if not s.query(Site).filter(Site.site == "runner_zero_probe").first():
            s.add(Site(site="runner_zero_probe", brand="Probe", country="US",
                       url="https://example.com", platform="generic",
                       proxy_tier="none"))
            s.commit()
    finally:
        s.close()

    monkeypatch.setattr("app.runner.get_crawler", lambda site: _ZeroCrawler())
    job_id = enqueue("runner_zero_probe")

    result = execute_job(job_id)

    s = SessionLocal()
    try:
        job = s.get(CrawlJob, job_id)
        assert result["status"] == "failed"
        assert result["error"] == "sitemap timeout"
        assert result["failure_code"] == "network_timeout"
        assert result["suggested_action"] == "检查代理后重跑"
        assert job.status == "failed"
        assert job.failure_code == "network_timeout"
        assert job.products_count == 0
    finally:
        s.close()


def test_execute_job_products_with_failure_becomes_partial(monkeypatch):
    init_db()
    s = SessionLocal()
    try:
        if not s.query(Site).filter(Site.site == "runner_partial_probe").first():
            s.add(Site(site="runner_partial_probe", brand="Probe", country="US",
                       url="https://example.com", platform="generic",
                       proxy_tier="none"))
            s.commit()
    finally:
        s.close()

    monkeypatch.setattr("app.runner.get_crawler", lambda site: _PartialCrawler())
    job_id = enqueue("runner_partial_probe")

    result = execute_job(job_id)

    s = SessionLocal()
    try:
        job = s.get(CrawlJob, job_id)
        assert result["status"] == "partial"
        assert result["products"] == 1
        assert result["failure_code"] == "http_429"
        assert result["error"] == "rate limited after first page"
        assert result["suggested_action"] == "降低并发和频率，延长冷却时间或更换代理出口"
        assert job.status == "partial"
        assert job.products_count == 1
        assert job.failure_code == "http_429"
        assert job.failure_stage == "fetch"
    finally:
        s.close()


def test_execute_job_applies_configured_price_feed(monkeypatch, tmp_path):
    init_db()
    feed = tmp_path / "prices.csv"
    feed.write_text(
        "sku,price,regular_price,currency\n"
        "FEED-1,19.99,29.99,USD\n",
        encoding="utf-8",
    )

    class _FeedCrawler:
        job_id: int | None = None

        def crawl(self) -> CrawlResult:
            out = CrawlResult()
            out.products.append({
                "sku": "FEED-1",
                "title": "Feed Product",
                "site": "runner_feed_probe",
                "product_url": "https://example.com/products/feed-1",
            })
            return out

    s = SessionLocal()
    try:
        s.query(Product).filter(Product.site == "runner_feed_probe").delete()
        s.query(Site).filter(Site.site == "runner_feed_probe").delete()
        s.add(Site(
            site="runner_feed_probe",
            brand="Probe",
            country="US",
            url="https://example.com",
            platform="generic",
            proxy_tier="none",
            crawler_config={
                "price_source_type": "feed",
                "price_feed_url": str(feed),
            },
        ))
        s.commit()
    finally:
        s.close()

    monkeypatch.setattr("app.runner.get_crawler", lambda site: _FeedCrawler())
    job_id = enqueue("runner_feed_probe")

    result = execute_job(job_id)

    s = SessionLocal()
    try:
        product = (s.query(Product)
                   .filter(Product.site == "runner_feed_probe",
                           Product.sku == "FEED-1")
                   .one())
        assert result["status"] == "success"
        assert result["products"] == 1
        assert product.sale_price == 19.99
        assert product.original_price == 29.99
        assert product.currency == "USD"
        assert any("configured_price_source: matched=1" in note
                   for note in result["notes"])
    finally:
        s.close()


def test_enqueue_auto_job_skips_when_required_proxy_unavailable(monkeypatch):
    init_db()
    from app import proxy_pool

    monkeypatch.setattr(proxy_pool, "has_available_proxy",
                        lambda tier, site=None: False)
    s = SessionLocal()
    try:
        s.query(CrawlJob).delete()
        s.query(Site).filter(Site.site == "runner_proxy_probe").delete()
        s.add(Site(site="runner_proxy_probe", brand="Probe", country="US",
                   url="https://example.com", platform="generic",
                   proxy_tier="residential"))
        s.commit()
    finally:
        s.close()

    job_id = enqueue("runner_proxy_probe", trigger="scheduled")

    s = SessionLocal()
    try:
        job = s.get(CrawlJob, job_id)
        failure = (s.query(CrawlFailure)
                   .filter(CrawlFailure.job_id == job_id)
                   .order_by(CrawlFailure.id.desc())
                   .first())
        assert job.status == "skipped"
        assert job.failure_code == "proxy_unavailable"
        assert failure is not None
        assert failure.code == "proxy_unavailable"
    finally:
        s.close()


def test_claim_job_skips_proxy_preflight_failure_and_claims_next(monkeypatch):
    init_db()
    from app import proxy_pool

    def fake_available(tier, site=None):
        return site != "runner_proxy_blocked"

    monkeypatch.setattr(proxy_pool, "has_available_proxy", fake_available)
    now = datetime.utcnow()
    s = SessionLocal()
    try:
        s.query(CrawlJob).delete()
        for site_name in ("runner_proxy_blocked", "runner_proxy_ready"):
            s.query(Site).filter(Site.site == site_name).delete()
        s.add(Site(site="runner_proxy_blocked", brand="Probe", country="US",
                   url="https://example.com/blocked", platform="generic",
                   proxy_tier="residential"))
        s.add(Site(site="runner_proxy_ready", brand="Probe", country="US",
                   url="https://example.com/ready", platform="generic",
                   proxy_tier="none"))
        s.flush()
        blocked = CrawlJob(site="runner_proxy_blocked", status="pending",
                           trigger="scheduled", created_at=now)
        ready = CrawlJob(site="runner_proxy_ready", status="pending",
                         trigger="scheduled",
                         created_at=now + timedelta(seconds=1))
        s.add_all([blocked, ready])
        s.commit()
        blocked_id = blocked.id
        ready_id = ready.id
    finally:
        s.close()

    assert claim_job("worker-test") == ready_id

    s = SessionLocal()
    try:
        blocked = s.get(CrawlJob, blocked_id)
        ready = s.get(CrawlJob, ready_id)
        assert blocked.status == "skipped"
        assert blocked.failure_code == "proxy_unavailable"
        assert ready.status == "running"
        assert ready.heartbeat_at is not None
    finally:
        s.close()
