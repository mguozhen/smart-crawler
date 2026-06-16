"""Trustpilot 评论采集器 —— 模块二。

Trustpilot 是 Next.js 站，AWS WAF / Cloudflare 防护。用 Scrapling 的
StealthyFetcher（Camoufox 隐身浏览器）突破，评论数据在页面 `__NEXT_DATA__`。

注意：Trustpilot 对数据中心 / 被标记网段 IP 直接 403 —— 实测我方 AT&T 网段
被拦（与 Vidaxl 同因）。须配住宅代理（proxies.txt 的 [residential] 段），
见 docs/风控策略评估.md。代理到位后此采集器即可工作。

批D 收编（2026-06）：
  - 继承 BaseCrawler（从 channel 合成 Site，同 reviews_io/trustedshops 模式）
  - 每页 StealthyFetcher.fetch 用 count_browser_fetch 包裹，成功（status==200）计 browser_opens
  - stealth 定制参数（stealth_kwargs/persist_profile_key 等）原样保留
  - 构造签名 / crawl 返回类型不变（向后兼容 review_runner）
"""
from __future__ import annotations

import json
import re

from .base import BaseCrawler
from ..models import Site

_ND_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


class TrustpilotCrawler(BaseCrawler):
    platform = "trustpilot"

    def __init__(self, channel: dict, max_pages: int = 10):
        # 从 channel 合成 Site，供 BaseCrawler 使用（同 reviews_io/trustedshops 模式）
        site = Site(
            site=channel.get("site") or "trustpilot",
            url=f"https://{channel.get('host', 'www.trustpilot.com')}",
            country=channel.get("country"),
            platform="trustpilot",
            proxy_tier="residential",
        )
        super().__init__(site)
        self.channel = channel
        self.domain = channel["domain"]
        self.host = channel.get("host", "www.trustpilot.com")
        self.max_pages = channel.get("max_pages", max_pages)
        self.notes: list[str] = []

    def crawl(self) -> list[dict]:                  # type: ignore[override]
        """返回标准化的评论 dict 列表（review_runner 直接调用此接口）。"""
        try:
            from scrapling.fetchers import StealthyFetcher
        except Exception as exc:
            self.notes.append(f"Scrapling 未安装: {exc}")
            return []

        reviews: list[dict] = []
        for page in range(1, self.max_pages + 1):
            url = f"https://{self.host}/review/{self.domain}?page={page}"
            try:
                from ._stealth_config import stealth_kwargs
                kw = stealth_kwargs(
                    proxy=self.proxy,
                    country=getattr(self, "country", None),
                    persist_profile_key=f"trustpilot_{self.domain}",
                )

                # 批D：每页 StealthyFetcher.fetch 用 count_browser_fetch 包裹，
                # 成功标准：status == 200；stealth 定制参数原样保留。
                fetched = self.count_browser_fetch(
                    lambda: StealthyFetcher.fetch(url, **kw),
                    success=lambda p: getattr(p, "status", None) == 200,
                )
            except Exception as exc:
                self.notes.append(f"page{page} 抓取异常: {exc}")
                break
            status = getattr(fetched, "status", None)
            if status != 200:
                self.notes.append(
                    f"page{page} HTTP {status}"
                    + ("（被拦截——需住宅代理）" if status == 403 else ""))
                break
            data = self._next_data(fetched.html_content)
            page_reviews = self._extract(data)
            if not page_reviews:
                break
            reviews.extend(page_reviews)
        self.notes.append(f"采集 {len(reviews)} 条评论")
        return reviews

    def _next_data(self, html: str) -> dict:
        m = _ND_RE.search(html or "")
        if not m:
            return {}
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            return {}

    def _extract(self, data: dict) -> list[dict]:
        """从 __NEXT_DATA__ 定位 reviews 列表并标准化。"""
        raw = self._find_reviews(data)
        out = []
        for r in raw or []:
            if not isinstance(r, dict):
                continue
            consumer = r.get("consumer") or {}
            dates = r.get("dates") or {}
            reply = r.get("reply") or {}
            labels = r.get("labels") or {}
            verification = (labels.get("verification") or {})
            rid = r.get("id") or r.get("reviewId")
            if not rid:
                continue
            out.append({
                "review_id": str(rid),
                "platform": "trustpilot",
                "site": self.channel.get("site") or self.site.site,
                "reviewer_name": consumer.get("displayName"),
                "reviewer_country": consumer.get("countryCode"),
                "rating": r.get("stars") or r.get("rating"),
                "title": r.get("title"),
                "content": r.get("text"),
                "language": r.get("language"),
                "review_date": dates.get("publishedDate"),
                "purchase_date": dates.get("experiencedDate"),
                "reply_content": reply.get("message"),
                "reply_date": reply.get("publishedDate"),
                "is_verified": bool(verification.get("isVerified")),
                "review_topics": r.get("labels", {}).get("merged")
                or r.get("tags"),
            })
        return out

    @staticmethod
    def _find_reviews(obj, depth: int = 0):
        """递归在 __NEXT_DATA__ 里找 reviews 数组。"""
        if depth > 8 or obj is None:
            return None
        if isinstance(obj, dict):
            rv = obj.get("reviews")
            if isinstance(rv, list) and rv and isinstance(rv[0], dict) \
                    and ("text" in rv[0] or "stars" in rv[0]):
                return rv
            for v in obj.values():
                res = TrustpilotCrawler._find_reviews(v, depth + 1)
                if res:
                    return res
        elif isinstance(obj, list):
            for v in obj[:20]:
                res = TrustpilotCrawler._find_reviews(v, depth + 1)
                if res:
                    return res
        return None
