"""美客多(MercadoLibre)按需采集器。

实测验证(2026-06-05,住宅代理 + 真浏览器 + 滚动等待):**可用**。
  关键配方:items API 已强制 OAuth(403),改走商品页真浏览器渲染。但价格/评论是
  懒加载,**必须滚动 + 等待**才会出现(只 fetch 不滚动只拿到无价格的壳页)。
    · listing 数据源 = 页面里的 JSON-LD(<script type="application/ld+json"> 的
      Product 块,schema.org 标准格式,最干净):
        title  = name
        price  = offers.price          currency = offers.priceCurrency
        avail  = offers.availability    brand    = brand(字符串或 {name})
        rating = aggregateRating.ratingValue   review_count = aggregateRating.reviewCount
        image  = image(字符串或数组)    sku = sku / productID
    · 评论原文 = DOM:article.ui-review-capability-comments__comment
        星级 = 数 __comment__rating__star 的 svg 个数(或读 "Calificación N de 5")
        正文 = p.ui-review-capability-comments__comment__content
反爬:     强,强制住宅代理(proxy_tier=residential)+ 真浏览器 + 滚动;裸 IP / 无滚动
          会被弹到 /gz/account-verification 或只拿到壳页。
          ⚠️ 稳定性:住宅 IP 信誉时好时坏,同一商品有时第 1 次就成、有时连续几次被弹
          验证页;runner 已切代理重试 _MAX_RETRY 次。实测可稳定跑通(价格 208739 ARS +
          评分 4.6 + 评论原文 → 入库),但生产高频抓取建议偏好已验证放行的代理区域。
URL->id:  商品页 URL 含 MLM-/MLB-/MLA- 编码(catalog 用 /p/MLA…;单卖家 wid=MLA…)。
"""
from __future__ import annotations

import json
import re

from ..antiban import BlockedError
from .base import BaseOnDemand

_ID_RE = re.compile(r"(ML[A-Z])-?(\d+)")
_LD_RE = re.compile(
    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
# 评论:正文 p.__comment__content;星级取正文前最近的 "Calificación N de 5"
_REVIEW_BODY_RE = re.compile(
    r'ui-review-capability-comments__comment__content[^>]*>([^<]+)</')
_REVIEW_CALIF_RE = re.compile(r'Calificaci[oó]n\s+(\d)\s+de\s+5')
PLATFORM = "mercadolibre"
SITE = f"ondemand_{PLATFORM}"


def _ld_product(html: str) -> dict | None:
    """从页面 HTML 抽出 JSON-LD 里 @type=Product 的块。"""
    for block in _LD_RE.findall(html):
        try:
            d = json.loads(block.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(d, dict) and d.get("@type") == "Product":
            return d
    return None


class MercadoLibreOnDemand(BaseOnDemand):
    platform = PLATFORM
    proxy_tier = "residential"

    @staticmethod
    def parse_item_id(url: str) -> str:
        m = _ID_RE.search(url)
        if not m:
            raise ValueError(f"美客多 URL 无商品编码: {url}")
        return (m.group(1) + m.group(2)).upper()

    @staticmethod
    def parse_listing(html: str, url: str) -> dict:
        """从渲染后的页面 HTML 解析 listing(数据源:JSON-LD Product 块)。"""
        d = _ld_product(html)
        if not d:
            raise BlockedError("ml/pdp 未找到 JSON-LD Product(疑似壳页/未渲染)")
        offers = d.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        brand = d.get("brand")
        if isinstance(brand, dict):
            brand = brand.get("name")
        img = d.get("image")
        if isinstance(img, str):
            imgs = [img]
        elif isinstance(img, list):
            imgs = [u for u in img if isinstance(u, str)]
        else:
            imgs = []
        agg = d.get("aggregateRating") or {}
        avail = str(offers.get("availability") or "")
        return {
            "sku": d.get("sku") or d.get("productID"),
            "title": d.get("name"),
            "sale_price": offers.get("price"),
            "original_price": offers.get("price"),
            "currency": offers.get("priceCurrency"),
            "image_urls": imgs,
            "ratings": agg.get("ratingValue"),
            "review_count": agg.get("reviewCount") or agg.get("ratingCount"),
            "status": "on_sale" if "InStock" in avail else "out_of_stock",
            "product_url": url,
            "site": SITE,
            "brand": brand or PLATFORM,
        }

    @staticmethod
    def parse_reviews(html: str, item_id, url: str) -> list[dict]:
        """从渲染后的页面 HTML 解析评论原文(数据源:评论 DOM)。

        每条评论 = 一段正文 p.__content;其星级取**正文之前最近的**
        "Calificación N de 5" 隐藏文本(article 块切分会错位,故按正文锚点回溯)。
        """
        sku = item_id[0] if isinstance(item_id, tuple) else item_id
        out = []
        # 先收集所有星级标注的位置,再为每条正文回溯最近的星级
        califs = [(m.start(), int(m.group(1)))
                  for m in _REVIEW_CALIF_RE.finditer(html)]
        for idx, mb in enumerate(_REVIEW_BODY_RE.finditer(html), start=1):
            content = mb.group(1).strip()
            if not content:
                continue
            pos = mb.start()
            # 该正文之前最近的一个 Calificación 即其星级
            rating = None
            for cpos, cval in califs:
                if cpos < pos:
                    rating = cval
                else:
                    break
            out.append({
                # 美客多评论 DOM 无稳定 id,用 sku + 序号合成唯一键
                "review_id": f"{sku}_{idx}",
                "platform": SITE,
                "site": SITE,
                "reviewer_name": None,
                "rating": rating,
                "title": None,
                "content": content,
                "review_date": None,
                "sku": sku,
                "product_url": url,
            })
        return out

    # ---- HTTP(真浏览器渲染,smoke 路径)----
    def _render(self, url: str, proxy=None) -> str:
        """住宅代理 + 真浏览器 + 滚动等待渲染。价格/评论懒加载,必须滚动。"""
        from scrapling.fetchers import StealthyFetcher

        from ..crawlers._stealth_config import stealth_kwargs

        def _scroll(page):
            try:
                for y in (3000, 6000, 9000, 12000):
                    page.mouse.wheel(0, y)
                    page.wait_for_timeout(1800)
            except Exception:
                pass
            return page

        kw = stealth_kwargs(proxy=proxy, country="AR", solve_cloudflare=True,
                            network_idle=True, timeout_ms=90000,
                            extra={"wait": 4000, "page_action": _scroll})
        page = StealthyFetcher.fetch(url, **kw)
        html = page.html_content or page.body or ""
        if "account-verification" in html[:3000]:
            raise BlockedError("ml/pdp 被弹到账号验证页(代理 IP 信誉不足)")
        return html

    def fetch_listing(self, item_id: str, url: str, proxy=None) -> dict:
        html = self._render(url, proxy=proxy)
        listing = self.parse_listing(html, url)
        # 顺带缓存本次 HTML,供同一 url 的 fetch_reviews 复用,避免二次渲染
        self._last_html = (url, html)
        return listing

    def fetch_reviews(self, item_id: str, url: str, limit: int = 100,
                      proxy=None) -> list[dict]:
        cached = getattr(self, "_last_html", None)
        if cached and cached[0] == url:
            html = cached[1]
        else:
            html = self._render(url, proxy=proxy)
        return self.parse_reviews(html, item_id, url)[:limit]

    def enumerate_listing(self, url: str, max_items: int = 100,
                          proxy=None) -> list[str]:
        """列表/搜索页枚举 itemId(渲染后从页面内 ML 编码兜底)。"""
        html = self._render(url, proxy=proxy)
        ids = []
        for m in _ID_RE.finditer(html):
            iid = (m.group(1) + m.group(2)).upper()
            if iid not in ids:
                ids.append(iid)
            if len(ids) >= max_items:
                break
        return ids
