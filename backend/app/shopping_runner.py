"""Google Shopping 编排 —— 模块四（规格 §4.4）。

关键词管理 + 搜索结果采集入库。
"""
from __future__ import annotations

from datetime import datetime

from .crawlers.google_shopping import GoogleShoppingCrawler
from .db import session_scope
from .models import Keyword, ShoppingResult


def import_keywords(words: list[str]) -> dict:
    """批量导入关键词，完全一致的跳过去重（规格 F4-001/004）。"""
    added, skipped = 0, 0
    with session_scope() as s:
        existing = {k.keyword for k in s.query(Keyword.keyword).all()
                    if k.keyword}
        for w in words:
            w = (w or "").strip()
            if not w:
                continue
            if w in existing:
                skipped += 1
                continue
            s.add(Keyword(keyword=w))
            existing.add(w)
            added += 1
    return {"added": added, "skipped": skipped}


def crawl_keyword(keyword: str) -> dict:
    """采集单个关键词的 Google Shopping 结果（F4-010/030）。"""
    crawler = GoogleShoppingCrawler(keyword)
    results = crawler.crawl()
    now = datetime.utcnow()
    with session_scope() as s:
        # 替换该关键词的旧结果
        s.query(ShoppingResult).filter(
            ShoppingResult.keyword == keyword).delete()
        for r in results:
            s.add(ShoppingResult(crawled_time=now, **r))
        kw = s.query(Keyword).filter(Keyword.keyword == keyword).first()
        if kw is None:
            kw = Keyword(keyword=keyword)
            s.add(kw)
        kw.last_crawled = now
        kw.result_count = len(results)
    return {"keyword": keyword, "results": len(results),
            "notes": crawler.notes}


def crawl_all_keywords() -> list[dict]:
    with session_scope() as s:
        words = [k.keyword for k in s.query(Keyword).all()]
    return [crawl_keyword(w) for w in words]


def competitor_share(keyword: str | None = None) -> list[dict]:
    """竞品商家占有率 —— 规格 F4-031。按商家统计出现比重。"""
    with session_scope() as s:
        q = s.query(ShoppingResult)
        if keyword:
            q = q.filter(ShoppingResult.keyword == keyword)
        rows = q.all()
        total = len(rows) or 1
        agg: dict[str, int] = {}
        for r in rows:
            m = r.merchant or "（未知）"
            agg[m] = agg.get(m, 0) + 1
    return sorted(
        ({"merchant": m, "count": c, "share": round(c / total * 100, 1)}
         for m, c in agg.items()),
        key=lambda x: -x["count"])
