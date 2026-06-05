"""Lazada 按需采集器。

实测验证(2026-06-05,真实站点逆向):
  listing:  商品页内嵌 JSON `__moduleData__`。curl_cffi 只能拿到反爬占位页,
            **必须用真浏览器渲染**(scrapling StealthyFetcher),否则无数据。
            真实字段路径:
              itemId   = data.root.fields.primaryKey.itemId
              skuId    = data.root.fields.primaryKey.skuId
              title    = data.root.fields.product.title
              brand    = data.root.fields.product.brand.name
              price    = data.root.fields.tracking.pdt_price  (形如 "RM114.00")
  reviews:  GET https://my.lazada.<tld>/pdp/review/getReviewList?itemId=..&pageNo=N
            注意 host 是 `my.` 子域(不是 www.);curl_cffi 直连即可,但**裸 IP 连打会被
            降级返回 HTML**,生产须住宅代理 + 限速。真实字段:
              model.items[].reviewRateId / rating / reviewContent / reviewTime / buyerName
              model.paging  = {totalItems, totalPages, currentPage}
              model.ratings = {average, rateCount, reviewCount}
  URL→id:   /products/<slug>-i<itemId>(-s<skuId>)?.html
反爬:       中-高,默认住宅代理(proxy_tier=residential)。
"""
from __future__ import annotations

import json
import re

from curl_cffi import requests as creq

from ..antiban import BlockedError, check_blocked
from .base import BaseOnDemand

_ID_RE = re.compile(r"-i(\d+)(?:-s\d+)?\.html")
# __moduleData__ 是一个大型嵌套 JSON,内部含大量 `};`,不能用非贪婪正则截断。
# 改为定位起始 `{` 后做括号配平提取(见 _extract_module_data)。
_MODULE_START_RE = re.compile(r"__moduleData__\s*=\s*\{")
# pdt_price 形如 "RM114.00" / "$1,299.00" / "1.234,56" —— 抽出数字部分
_PRICE_NUM_RE = re.compile(r"[\d.,]+")
PLATFORM = "lazada"
SITE = f"ondemand_{PLATFORM}"


def _clean_review_text(s):
    """评论文本 best-effort 修复 Lazada 的 emoji mojibake。

    仅当整串能用 latin1->utf-8 干净反解时才修复(纯 mojibake 串);否则原样返回。
    既能还原完整的 mojibake emoji,又绝不破坏正常的 CJK / 重音文字。
    源头已损坏 / 截断的 emoji 字节无法还原,保持原样不强行剥除。
    """
    if not s:
        return s
    try:
        return s.encode("latin1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def _to_float(v):
    """把 'RM114.00' / '1,299.00' / 114.0 统一成 float。"""
    if v is None:
        return None
    m = _PRICE_NUM_RE.search(str(v))
    if not m:
        return None
    raw = m.group(0)
    # 处理千分位:若同时有 '.' 和 ','，最后出现的那个当小数点
    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    else:
        raw = raw.replace(",", "")
    try:
        return float(raw)
    except ValueError:
        return None


def _extract_module_data(html: str) -> dict | None:
    """从页面 HTML 中按括号配平提取 __moduleData__ 的完整 JSON 对象。"""
    m = _MODULE_START_RE.search(html)
    if not m:
        return None
    start = m.end() - 1            # 指向起始 '{'
    depth, in_str, esc = 0, False, False
    for i in range(start, len(html)):
        c = html[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[start:i + 1])
                except json.JSONDecodeError:
                    return None
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
    def _fields(data: dict) -> dict:
        return (data.get("data", {}).get("root", {}).get("fields", {})) or {}

    @staticmethod
    def parse_listing(data: dict, url: str) -> dict:
        f = LazadaOnDemand._fields(data)
        pk = f.get("primaryKey", {}) or {}
        product = f.get("product", {}) or {}
        tracking = f.get("tracking", {}) or {}
        brand = product.get("brand") or {}

        price = _to_float(tracking.get("pdt_price"))
        # 货币:从 pdt_price 的字母前缀粗略推断(RM/$/€…),拿不到就留空
        cur = None
        pm = re.match(r"\s*([A-Za-z$€£¥]{1,3})", str(tracking.get("pdt_price") or ""))
        if pm:
            cur = pm.group(1)

        # 图片:skuGalleries 是 {skuId: [{type, poster, image}, ...]}。
        # 真实数据里图片 URL 在 `poster`(image 恒为 None),含 video 元素需过滤,
        # 且 URL 可能是 `//` 协议相对,补成 https。
        imgs: list[str] = []
        gal = f.get("skuGalleries") or {}
        if isinstance(gal, dict):
            for arr in gal.values():
                if not isinstance(arr, list):
                    continue
                for g in arr:
                    if not isinstance(g, dict) or g.get("type") == "video":
                        continue
                    u = g.get("poster") or g.get("image")
                    if not u:
                        continue
                    if u.startswith("//"):
                        u = "https:" + u
                    if u not in imgs:
                        imgs.append(u)
                if imgs:
                    break

        return {
            "sku": pk.get("itemId"),
            "title": product.get("title"),
            "sale_price": price,
            "original_price": price,        # 内嵌数据无划线原价,等于售价
            "currency": cur,
            "image_urls": imgs,
            "variant_id": pk.get("skuId"),
            "description": product.get("desc"),
            "status": "on_sale",            # 商品页可渲染即视为在售
            "product_url": url,
            "site": SITE,
            "brand": (brand.get("name") if isinstance(brand, dict) else None) or PLATFORM,
        }

    @staticmethod
    def parse_reviews(data: dict, item_id, url: str) -> list[dict]:
        model = data.get("model", {}) or {}
        out = []
        for r in (model.get("items") or []):
            rid = r.get("reviewRateId")
            out.append({
                "review_id": str(rid) if rid is not None else None,
                "platform": SITE,
                "site": SITE,
                "reviewer_name": r.get("buyerName"),
                "rating": r.get("rating"),
                "title": _clean_review_text(r.get("reviewTitle")),
                "content": _clean_review_text(r.get("reviewContent")),
                "review_date": r.get("reviewTime"),
                "sku": item_id if not isinstance(item_id, tuple) else item_id[0],
                "product_url": url,
            })
        return out

    # ---- HTTP(smoke 路径)----
    def _render(self, url: str, proxy=None) -> str:
        """真浏览器渲染页面,返回 HTML。Lazada listing 必须走这条。"""
        from scrapling.fetchers import StealthyFetcher

        from ..crawlers._stealth_config import stealth_kwargs
        kw = stealth_kwargs(proxy=proxy, country="MY", solve_cloudflare=True,
                            network_idle=True, timeout_ms=90000)
        page = StealthyFetcher.fetch(url, **kw)
        return page.html_content or page.body or ""

    def fetch_listing(self, item_id: str, url: str, proxy=None) -> dict:
        html = self._render(url, proxy=proxy)
        data = _extract_module_data(html)
        if data is None:
            # 渲染拿到的是占位/挑战页 —— 当作被封,交给 runner 切代理重试
            raise BlockedError("lazada/pdp 未渲染出 __moduleData__(疑似反爬占位页)")
        return self.parse_listing(data, url)

    def _review_host(self, url: str) -> str:
        """评论接口在 my.<region> 子域。把 www.lazada.com.my → my.lazada.com.my。"""
        host = re.sub(r"^https?://", "", url).split("/")[0]
        host = re.sub(r"^www\.", "", host)
        return host if host.startswith("my.") else "my." + host

    def fetch_reviews(self, item_id: str, url: str, limit: int = 100,
                      proxy=None) -> list[dict]:
        host = self._review_host(url)
        out, page = [], 1
        while len(out) < limit and page <= 20:
            api = (f"https://{host}/pdp/review/getReviewList"
                   f"?itemId={item_id}&pageSize=20&filter=0&sort=0&pageNo={page}")
            s = creq.Session(impersonate="chrome")
            if proxy:
                s.proxies = {"http": proxy, "https": proxy}
            s.headers.update({"Referer": url, "X-Requested-With": "XMLHttpRequest",
                              "Accept": "application/json"})
            resp = s.get(api, timeout=40)
            check_blocked(resp.status_code, "lazada/reviews")
            resp.raise_for_status()
            if "json" not in resp.headers.get("content-type", ""):
                # 裸 IP 被降级返回 HTML —— 当作被封,交给 runner 切代理重试
                raise BlockedError("lazada/reviews 返回非 JSON(疑似 IP 限速)")
            batch = self.parse_reviews(resp.json(), item_id, url)
            if not batch:
                break
            out.extend(batch)
            page += 1
        return out[:limit]

    def enumerate_listing(self, url: str, max_items: int = 100, proxy=None):
        html = self._render(url, proxy=proxy)
        ids = []
        for m in _ID_RE.finditer(html):
            if m.group(1) not in ids:
                ids.append(m.group(1))
            if len(ids) >= max_items:
                break
        return ids
