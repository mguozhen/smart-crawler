"""Lazada 按需采集器。

listing:  商品页内嵌 JSON(__moduleData__ / pdp data)解析;HTTP 取页面后正则抠 JSON。
reviews:  GET https://my.lazada.com.my/pdp/review/getReviewList?itemId=...&pageNo=N
URL→id:   /products/<slug>-i<itemId>-s<skuId>.html
反爬:     中-高,默认住宅代理(proxy_tier=residential),有滑块风险。
"""
from __future__ import annotations

import json
import re

from curl_cffi import requests as creq

from ..antiban import check_blocked
from .base import BaseOnDemand

_ID_RE = re.compile(r"-i(\d+)(?:-s\d+)?\.html")
# 非贪婪 {.*?} 仅适配简单内嵌 JSON;真实 PDP 的 __moduleData__ 可能嵌套含 `};`,
# 需在 smoke(Task 11)中按实际页面校验/改用括号配平提取。
_MODULE_RE = re.compile(r"__moduleData__\s*=\s*(\{.*?\});", re.S)
PLATFORM = "lazada"
SITE = f"ondemand_{PLATFORM}"


def _to_float(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


class LazadaOnDemand(BaseOnDemand):
    platform = PLATFORM
    proxy_tier = "residential"

    @staticmethod
    def parse_item_id(url: str) -> str:
        m = _ID_RE.search(url)
        if not m:
            raise ValueError(f"Lazada URL 无 itemId: {url}")
        return m.group(1)

    @staticmethod
    def _first_item(data: dict) -> dict:
        items = (data.get("data", {}).get("root", {}).get("fields", {})
                 .get("product", {}).get("items", []))
        return items[0] if items else {}

    @staticmethod
    def parse_listing(data: dict, url: str) -> dict:
        it = LazadaOnDemand._first_item(data)
        imgs = it.get("images") or ([it["image"]] if it.get("image") else [])
        return {
            "sku": it.get("itemId"),
            "title": it.get("name"),
            "sale_price": _to_float(it.get("price")),
            "original_price": _to_float(it.get("originalPrice")) or _to_float(it.get("price")),
            "currency": it.get("currency"),
            "image_urls": imgs,
            "variant_id": it.get("skuId"),
            "inventory": str(it.get("stock")) if it.get("stock") is not None else None,
            "status": ("on_sale" if (it.get("stock") or 0) > 0 else "out_of_stock"),
            "product_url": url,
            "site": SITE,
            "brand": PLATFORM,
        }

    @staticmethod
    def parse_reviews(data: dict, item_id, url: str) -> list[dict]:
        out = []
        for r in (data.get("model", {}).get("items") or []):
            out.append({
                "review_id": r.get("reviewId"),
                "platform": SITE,
                "site": SITE,
                "reviewer_name": r.get("buyerName"),
                "rating": r.get("rating"),
                "title": r.get("reviewTitle"),
                "content": r.get("reviewContent"),
                "review_date": r.get("reviewTime"),
                "sku": item_id,
                "product_url": url,
            })
        return out

    # ---- HTTP(smoke 路径)----
    def _session(self, proxy):
        s = creq.Session(impersonate="chrome")
        if proxy:
            s.proxies = {"http": proxy, "https": proxy}
        return s

    def fetch_listing(self, item_id: str, url: str, proxy=None) -> dict:
        s = self._session(proxy)
        resp = s.get(url, timeout=30)
        check_blocked(resp.status_code, "lazada/pdp")
        resp.raise_for_status()
        m = _MODULE_RE.search(resp.text)
        if not m:
            raise ValueError("Lazada PDP 未找到 __moduleData__")
        return self.parse_listing(json.loads(m.group(1)), url)

    def fetch_reviews(self, item_id: str, url: str, limit: int = 100,
                      proxy=None) -> list[dict]:
        s = self._session(proxy)
        host = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
        out, page = [], 1
        while len(out) < limit and page <= 20:
            api = (f"https://{host}/pdp/review/getReviewList"
                   f"?itemId={item_id}&pageSize=20&pageNo={page}")
            resp = s.get(api, timeout=30)
            check_blocked(resp.status_code, "lazada/reviews")
            resp.raise_for_status()
            batch = self.parse_reviews(resp.json(), item_id, url)
            if not batch:
                break
            out.extend(batch)
            page += 1
        return out[:limit]

    def enumerate_listing(self, url: str, max_items: int = 100, proxy=None):
        s = self._session(proxy)
        resp = s.get(url, timeout=30)
        check_blocked(resp.status_code, "lazada/listing")
        resp.raise_for_status()
        ids = []
        for m in _ID_RE.finditer(resp.text):
            if m.group(1) not in ids:
                ids.append(m.group(1))
            if len(ids) >= max_items:
                break
        return ids
