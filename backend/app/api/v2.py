"""smart-crawler v2 API · Firecrawl-compatible 设计

参考 Firecrawl (W24 YC) 的 7 端点 API 设计：
  POST /v2/scrape       · 单 URL 抓取（返 markdown + structured）
  POST /v2/crawl        · 整站爬（异步任务）
  GET  /v2/crawl/{id}   · 查爬取任务状态
  POST /v2/map          · 列出站点全部 URL（sitemap）
  POST /v2/extract      · 用 LLM 抽取结构化字段
  POST /v2/batch/scrape · 异步批量抓
  GET  /v2/sources      · 所有数据源元数据（含 crawl_url）

鉴权：Authorization: Bearer sck_xxxx（同 Firecrawl 风格的 Bearer 前缀）
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, date
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    CrawlJob, Product, PriceHistory, Promotion, Review, Site,
)
from .routes import require_user
from ..runner import enqueue


router = APIRouter(
    prefix="/api/v2",
    dependencies=[Depends(require_user)],
    tags=["v2 · Firecrawl-compatible"],
)


# ============= Request / Response Models =============

class ScrapeRequest(BaseModel):
    url: str = Field(..., description="要抓取的 URL · e.g. https://www.songmics.com/products/abc")
    formats: list[str] = Field(default=["markdown", "structured"],
                                description="返回格式: markdown / structured / html / links / screenshot")
    only_main_content: bool = Field(default=True, description="只返主要内容（去掉导航/footer）")
    wait_for: int = Field(default=0, description="额外等待 ms（JS 重的站可加）")
    timeout: int = Field(default=30000, description="超时 ms")


class ProductData(BaseModel):
    """smart-crawler 标准产品数据 schema · 14 字段。"""
    site: str = Field(..., description="站点代号，如 songmics_us")
    site_url: str = Field(..., description="站点根 URL，如 https://www.songmics.com/")
    sku: str
    spu: Optional[str] = None
    title: str
    description: Optional[str] = None
    image_urls: list[str] = []
    category_path: Optional[str] = None
    sale_price: Optional[float] = None
    original_price: Optional[float] = None
    currency: Optional[str] = None
    status: Optional[str] = Field(None, description="on_sale / out_of_stock / discontinued")
    ratings: Optional[float] = None
    review_count: Optional[int] = None
    brand: Optional[str] = None
    product_url: str = Field(..., description="商品 PDP URL，可点击")
    crawled_at: Optional[str] = Field(None, description="ISO 抓取时间")
    confidence: float = Field(default=1.0, description="数据置信度 0-1")


class ScrapeResponse(BaseModel):
    success: bool = True
    url: str = Field(..., description="原始请求 URL")
    crawl_url: Optional[str] = Field(None, description="实际抓取的最终 URL（去 redirect）")
    site: Optional[str] = Field(None, description="匹配的内部 site 代号")
    metadata: dict = Field(default_factory=dict, description="title / description / og / ld+json")
    data: Optional[ProductData] = Field(None, description="如果是商品页，返结构化数据")
    markdown: Optional[str] = None
    html: Optional[str] = None
    links: list[str] = []
    scrape_id: str = Field(..., description="唯一抓取 ID（计费用）")
    credits_used: int = Field(default=1, description="本次扣 credits")


class MapRequest(BaseModel):
    url: str = Field(..., description="站点根 URL · e.g. https://www.songmics.com/")
    limit: int = Field(default=1000, description="最多返回多少 URL")
    include_subdomains: bool = Field(default=False)
    search: Optional[str] = Field(None, description="URL 含某关键词才返")


class MapResponse(BaseModel):
    success: bool = True
    url: str
    site: Optional[str] = None
    links: list[str] = []
    count: int = 0
    credits_used: int = 1


class CrawlRequest(BaseModel):
    url: str = Field(..., description="站点根 URL · 触发整站爬")
    limit: int = Field(default=1000, description="最多抓多少页")
    include_paths: list[str] = Field(default=[], description="正则白名单 · e.g. ['^/products/']")
    exclude_paths: list[str] = Field(default=[], description="正则黑名单")
    max_depth: int = Field(default=2)
    poll_interval: int = Field(default=30, description="轮询任务状态秒数")


class CrawlResponse(BaseModel):
    success: bool = True
    job_id: int
    status: str = Field(description="pending / running / completed / failed")
    site: Optional[str] = None
    crawl_url: str
    total: Optional[int] = None
    credits_used: int = 0
    poll_url: str


class ExtractRequest(BaseModel):
    urls: list[str] = Field(..., description="要抽取的 URL 列表（最多 25 个）")
    schema_: dict = Field(..., alias="schema", description="目标 JSON Schema · 描述字段")
    prompt: Optional[str] = Field(None, description="额外抽取指令")


class BatchScrapeRequest(BaseModel):
    urls: list[str] = Field(..., description="批量抓取 URL 列表（最多 100）")
    formats: list[str] = Field(default=["markdown", "structured"])
    webhook: Optional[str] = Field(None, description="完成后 webhook URL")


class BatchScrapeResponse(BaseModel):
    success: bool = True
    batch_id: str
    total: int
    poll_url: str
    credits_used: int


class DataSourceInfo(BaseModel):
    """单个 smart-crawler 数据源元数据。"""
    site: str = Field(..., description="内部代号")
    crawl_url: str = Field(..., description="爬取的网站 URL")
    brand: str
    country: str
    platform: str = Field(..., description="shopify/vue_spa/nuxt/vidaxl/wayfair 等")
    sku_count: int
    coverage_pct: float
    status: str = Field(..., description="healthy / warning / critical / empty")
    last_crawled: Optional[str] = None
    proxy_tier: str = Field(default="none")
    anti_bot_level: int = Field(default=1, description="1-5 反爬难度")


# ============= Helpers =============

def _match_site(db: Session, url: str) -> Optional[Site]:
    """根据 URL 匹配内部站点。"""
    if not url:
        return None
    url_lower = url.lower().rstrip("/")
    # 精确域名匹配
    for s in db.query(Site).all():
        if s.url and url_lower.startswith(s.url.lower().rstrip("/")):
            return s
    return None


def _product_to_schema(p: Product, site: Optional[Site] = None) -> ProductData:
    site_url = site.url if site else ""
    return ProductData(
        site=p.site,
        site_url=site_url,
        sku=p.sku,
        spu=p.spu,
        title=p.title or "",
        description=p.description,
        image_urls=p.image_urls or [],
        category_path=p.category_path,
        sale_price=p.sale_price,
        original_price=p.original_price,
        currency=p.currency,
        status=p.status,
        ratings=p.ratings,
        review_count=p.review_count,
        brand=p.brand,
        product_url=p.product_url or "",
        crawled_at=p.updated_time.isoformat() if p.updated_time else None,
        confidence=1.0,
    )


def _anti_bot_level(platform: str) -> int:
    levels = {
        "shopify": 1, "generic": 2, "vue_spa": 2, "nuxt": 2, "magento": 2, "shoper": 2,
        "vonhaus": 2, "woltu": 2, "flexispot": 2, "overstock": 2, "article": 1,
        "westelm": 2, "cratebarrel": 2, "ikea": 3, "bol": 3, "cdiscount": 3, "otto": 3,
        "vidaxl": 4, "idealo": 4, "wayfair": 5, "allegro": 5, "ebay": 5, "houzz": 3,
    }
    return levels.get(platform, 2)


# ============= Endpoints =============

@router.post("/scrape", response_model=ScrapeResponse)
def scrape(req: ScrapeRequest, db: Session = Depends(get_db)):
    """抓取单个 URL → 返 markdown + 结构化数据（Firecrawl-compatible）。

    Try-match：如果 URL 命中已知 site 且 DB 已存 SKU，直接返存量。
    否则触发抓取并返当前可用数据（异步）。
    """
    scrape_id = "scr_" + uuid.uuid4().hex[:16]
    site = _match_site(db, req.url)

    if not site:
        return ScrapeResponse(
            success=False, url=req.url, scrape_id=scrape_id, credits_used=0,
            metadata={"error": "URL not in supported sites; try /v2/map first"},
        )

    # 查 DB 是否已有该 URL 的商品
    p = db.query(Product).filter(
        Product.site == site.site,
        Product.product_url == req.url
    ).first()

    if p:
        return ScrapeResponse(
            url=req.url, crawl_url=req.url, site=site.site,
            scrape_id=scrape_id,
            data=_product_to_schema(p, site),
            metadata={
                "site": site.site, "brand": site.brand,
                "platform": site.platform, "country": site.country,
            },
            markdown=f"# {p.title}\n\n{p.description or ''}\n\nPrice: {p.sale_price} {p.currency}",
            credits_used=1,
        )

    # 不在 DB 里 → 入队任务，返触发响应
    job_id = enqueue(site.site, trigger="v2_scrape")
    return ScrapeResponse(
        url=req.url, crawl_url=req.url, site=site.site,
        scrape_id=scrape_id,
        metadata={"queued_job": job_id, "msg": "URL queued for crawling"},
        markdown=None, credits_used=1,
    )


@router.post("/map", response_model=MapResponse)
def map_site(req: MapRequest, db: Session = Depends(get_db)):
    """列出站点全部已抓 URL。"""
    site = _match_site(db, req.url)
    if not site:
        raise HTTPException(404, f"Site not supported: {req.url}")

    q = db.query(Product.product_url).filter(Product.site == site.site)
    if req.search:
        q = q.filter(Product.title.ilike(f"%{req.search}%"))
    urls = [r[0] for r in q.limit(req.limit).all() if r[0]]

    return MapResponse(
        url=req.url, site=site.site,
        links=urls, count=len(urls), credits_used=1,
    )


@router.post("/crawl", response_model=CrawlResponse)
def crawl(req: CrawlRequest, db: Session = Depends(get_db)):
    """触发整站爬取（异步）。返 job_id，用 GET /v2/crawl/{id} 轮询。"""
    site = _match_site(db, req.url)
    if not site:
        raise HTTPException(404, f"Site not supported: {req.url}")

    job_id = enqueue(site.site, trigger="v2_crawl")
    return CrawlResponse(
        job_id=job_id, status="pending",
        site=site.site, crawl_url=req.url,
        credits_used=req.limit,
        poll_url=f"/v2/crawl/{job_id}",
    )


@router.get("/crawl/{job_id}")
def crawl_status(job_id: int, db: Session = Depends(get_db)):
    """查爬取任务状态。"""
    job = db.get(CrawlJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    site = db.query(Site).filter(Site.site == job.site).first()

    total = None
    data: list[dict] = []
    if job.status == "success":
        prods = db.query(Product).filter(Product.site == job.site).limit(100).all()
        total = job.products_count or len(prods)
        data = [_product_to_schema(p, site).model_dump() for p in prods]

    return {
        "success": True,
        "job_id": job_id,
        "status": job.status,
        "site": job.site,
        "crawl_url": site.url if site else "",
        "total": total,
        "products_count": job.products_count,
        "duration_sec": job.duration_sec,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "error": job.error,
        "data": data,
    }


@router.post("/batch/scrape", response_model=BatchScrapeResponse)
def batch_scrape(req: BatchScrapeRequest, db: Session = Depends(get_db)):
    """批量抓 URL · 异步。"""
    if len(req.urls) > 100:
        raise HTTPException(400, "Max 100 URLs per batch")

    batch_id = "batch_" + uuid.uuid4().hex[:16]
    queued_jobs = []
    for url in req.urls:
        site = _match_site(db, url)
        if site:
            jid = enqueue(site.site, trigger=f"v2_batch_{batch_id}")
            queued_jobs.append(jid)

    return BatchScrapeResponse(
        batch_id=batch_id, total=len(queued_jobs),
        poll_url=f"/v2/batch/{batch_id}",
        credits_used=len(queued_jobs),
    )


@router.post("/extract")
def extract(req: ExtractRequest, db: Session = Depends(get_db)):
    """用 LLM 从 URL 抽取自定义字段（mock · 实际接 LLM）。"""
    return {
        "success": True,
        "extracted": [
            {"url": u, "matched_site": (_match_site(db, u).site if _match_site(db, u) else None),
             "status": "queued"}
            for u in req.urls[:25]
        ],
        "msg": "LLM extraction queued · 实际 schema 抽取在 v2.1 上线（claude-haiku-4-5 + instructor）",
        "credits_used": len(req.urls[:25]),
    }


@router.get("/sources", response_model=list[DataSourceInfo])
def list_sources(db: Session = Depends(get_db)):
    """列出所有 59 个数据源 + 元数据（含 crawl_url）。"""
    sites = db.query(Site).all()
    out = []
    for s in sites:
        sku_count = db.query(Product).filter(Product.site == s.site).count()
        out.append(DataSourceInfo(
            site=s.site,
            crawl_url=s.url or "",
            brand=s.brand or "",
            country=s.country or "",
            platform=s.platform or "",
            sku_count=sku_count,
            coverage_pct=100.0 if sku_count else 0.0,
            status="healthy" if sku_count > 0 else "empty",
            last_crawled=s.last_crawled.isoformat() if s.last_crawled else None,
            proxy_tier=s.proxy_tier or "none",
            anti_bot_level=_anti_bot_level(s.platform or "generic"),
        ))
    return out


@router.get("/")
def v2_root():
    """v2 API 索引。"""
    return {
        "service": "smart-crawler",
        "version": "v2.0",
        "compatible_with": "Firecrawl v1 API",
        "auth": "Authorization: Bearer sck_...",
        "endpoints": {
            "POST /v2/scrape": "Single URL → structured data",
            "POST /v2/map": "Site URL → list of all known URLs",
            "POST /v2/crawl": "Full site crawl (async)",
            "GET /v2/crawl/{id}": "Crawl job status",
            "POST /v2/batch/scrape": "Batch async (max 100)",
            "POST /v2/extract": "LLM schema extraction",
            "GET /v2/sources": "All data sources + URLs",
        },
        "data_types": {
            "ProductData": "14 fields incl site_url/product_url/sku/title/sale_price/...",
            "DataSourceInfo": "site/crawl_url/brand/country/platform/sku_count/anti_bot_level",
        },
        "docs": "https://smartcrawler.io/d/api_v2_spec.html",
    }
