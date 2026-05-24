"""Shopping 搜索采集器 —— 模块四（规格 §4.4）。

不依赖任何第三方付费 SERP API。三路径合一：

1. **Google Shopping stealth** —— scrapling StealthyFetcher 用 real_chrome +
   mobile UA + cookie 预热，单次请求模拟真人。消费级 IP 大概率被 reCAPTCHA
   拦，但开着 stealth 一旦绕过 1 次可继续若干次。

2. **Bing Shopping** —— Bing 对爬虫宽容很多，几乎不拦。结构清晰，
   `https://www.bing.com/shop` 解析稳定。生产兜底用这个。

3. **结果合并去重** —— 两路并集（同标题+商家算同一条），优先用 Google 的
   position；Bing-only 的从 Google 最大 position+1 起接续。

字段对齐 ShoppingResult（规格 §4.4.4，15 字段）。
"""
from __future__ import annotations

import os
import re
import urllib.parse

from selectolax.parser import HTMLParser

from ..proxy import get_proxy

_PRICE_RE = re.compile(r"[\$£€¥]\s?([\d,]+(?:\.\d{1,2})?)")
_RATING_RE = re.compile(r"([0-5]\.\d)")
_REVIEW_NUM_RE = re.compile(r"\(?\s*([\d,]{2,})\s*\)?")
_DESKTOP_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
               "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 "
               "Safari/537.36")
_MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
              "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 "
              "Mobile/15E148 Safari/604.1")


class GoogleShoppingCrawler:
    """名为 GoogleShopping，实际"Google + Bing"合并采集。"""

    platform = "google_shopping"

    def __init__(self, keyword: str, max_results: int = 60):
        self.keyword = keyword
        self.max_results = max_results
        self.proxy = get_proxy("residential")
        self.notes: list[str] = []

    def crawl(self) -> list[dict]:
        out: list[dict] = []
        seen: set[tuple] = set()       # (title_norm, merchant_norm)

        # ---------- 路径 1：Google Shopping stealth ----------
        google_results = self._crawl_google_stealth()
        for r in google_results:
            k = (_norm(r.get("product_title")), _norm(r.get("merchant")))
            if k[0] and k not in seen:
                seen.add(k)
                out.append(r)
        if google_results:
            self.notes.append(f"Google Shopping: {len(google_results)} 个")

        # ---------- 路径 2：Bing Shopping ----------
        if len(out) < self.max_results:
            bing_results = self._crawl_bing()
            base_pos = max((r.get("position") or 0) for r in out) if out else 0
            added = 0
            for r in bing_results:
                k = (_norm(r.get("product_title")), _norm(r.get("merchant")))
                if not k[0] or k in seen:
                    continue
                seen.add(k)
                added += 1
                r["position"] = base_pos + added
                out.append(r)
                if len(out) >= self.max_results:
                    break
            if bing_results:
                self.notes.append(
                    f"Bing Shopping: {len(bing_results)} 个（新增 {added}）")

        self.notes.append(
            f"关键词「{self.keyword}」合并 {len(out)} 个商品")
        return out[: self.max_results]

    # ==================== Google Shopping ====================
    def _crawl_google_stealth(self) -> list[dict]:
        """用 scrapling StealthyFetcher 拿 Google Shopping 页。"""
        try:
            from scrapling.fetchers import StealthyFetcher
        except Exception as exc:
            self.notes.append(f"scrapling 未装: {exc}")
            return []

        url = ("https://www.google.com/search?tbm=shop&hl=en&gl=us&q="
               + urllib.parse.quote(self.keyword))

        def warm_then_search(page):
            """先访问 google.com 取 cookie，再滚动 shopping 页。"""
            try:
                page.wait_for_timeout(1500)
                page.mouse.wheel(0, 800)
                page.wait_for_timeout(1200)
                page.mouse.wheel(0, 2500)
                page.wait_for_timeout(1500)
                page.mouse.wheel(0, 3000)
                page.wait_for_timeout(1200)
            except Exception:
                pass
            return page

        from ._stealth_config import stealth_kwargs
        kw = stealth_kwargs(
            proxy=self.proxy,
            country="US",  # Google Shopping 默认走 US
            network_idle=False,
            timeout_ms=60000,
            persist_profile_key=f"gshop_{self.keyword[:20]}",
            extra={
                "wait": 1500,
                "useragent": _DESKTOP_UA,
                "humanize": True,
                "page_action": warm_then_search,
            },
        )
        try:
            page = StealthyFetcher.fetch(url, **kw)
        except Exception as exc:
            self.notes.append(f"Google fetch 异常: {str(exc)[:120]}")
            return []
        status = getattr(page, "status", None)
        if status != 200:
            self.notes.append(f"Google HTTP {status}")
            return []
        try:
            html = page.body if hasattr(page, "body") else page.html_content
        except Exception:
            html = str(page)
        if "captcha" in (html or "")[:5000].lower():
            self.notes.append("Google reCAPTCHA 拦截")
            return []
        return self._parse_google(html or "")

    def _parse_google(self, html: str) -> list[dict]:
        tree = HTMLParser(html)
        cards: list = []
        # 多套选择器兜底（Google 类名动态变化）
        for sel in ('div.sh-dgr__content',           # 经典桌面
                    'div.KZmu8e',                    # 2024 新结构
                    'div[role="listitem"]',
                    'div.MtXiu',
                    'div[data-docid]'):
            cards = tree.css(sel)
            if cards and len(cards) >= 3:
                break
        results, pos = [], 0
        for c in cards:
            text = c.text(separator="\n") or ""
            pm = _PRICE_RE.search(text)
            if not pm:
                continue
            pos += 1
            if pos > self.max_results:
                break
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            title = _pick_title(lines)
            link = _first_attr(c, "a", "href")
            img = _first_attr(c, "img", "src") or _first_attr(c, "img",
                                                              "data-src")
            merchant = _merchant_guess(lines)
            rating, reviews = _rating_reviews(text)
            results.append({
                "keyword": self.keyword, "position": pos,
                "product_title": title, "product_image": img,
                "price": float(pm.group(1).replace(",", "")),
                "currency": _currency_from(pm.group(0)),
                "merchant": merchant, "merchant_url": None,
                "product_url": _clean_google_link(link),
                "rating": rating, "review_count": reviews,
                "shipping_info": ("Free" if "free shipping" in text.lower()
                                  else None),
                "promotion_label": ("SALE" if re.search(
                    r"sale|deal|% off", text, re.I) else None),
            })
        return results

    # ==================== Bing Shopping ====================
    def _crawl_bing(self) -> list[dict]:
        """Bing Shopping 几乎不拦，curl_cffi 直拉即可。"""
        try:
            from curl_cffi import requests as creq
        except Exception as exc:
            self.notes.append(f"curl_cffi 未装: {exc}")
            return []

        url = ("https://www.bing.com/shop?q="
               + urllib.parse.quote(self.keyword) + "&cc=us&setlang=en-us")
        try:
            sess = creq.Session(impersonate="chrome")
            sess.headers.update({"User-Agent": _DESKTOP_UA,
                                 "Accept-Language": "en-US,en;q=0.9"})
            r = sess.get(url, timeout=20)
        except Exception as exc:
            self.notes.append(f"Bing fetch 异常: {str(exc)[:120]}")
            return []
        if r.status_code != 200:
            self.notes.append(f"Bing HTTP {r.status_code}")
            return []
        return self._parse_bing(r.text)

    def _parse_bing(self, html: str) -> list[dict]:
        tree = HTMLParser(html)
        cards: list = []
        for sel in ('li.br-item',                # 经典 PA card
                    'div.br-pdItem',
                    'div.slide.bpc_pd_grid',
                    'div[data-attrid="image"]',
                    'div.b_main'):
            cards = tree.css(sel)
            if cards and len(cards) >= 3:
                break
        results, pos = [], 0
        for c in cards:
            text = c.text(separator="\n") or ""
            pm = _PRICE_RE.search(text)
            if not pm:
                continue
            pos += 1
            if pos > self.max_results:
                break
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            # Bing 商品标题通常在 .br-title 或 .pdpItemTitle
            title_node = (c.css_first(".br-title") or c.css_first(".pdpItemTitle")
                          or c.css_first("h3"))
            title = (title_node.text(strip=True) if title_node else
                     _pick_title(lines))
            link = _first_attr(c, "a", "href")
            img = (_first_attr(c, "img", "src") or
                   _first_attr(c, "img", "data-src"))
            seller_node = (c.css_first(".br-sellersCite") or
                           c.css_first(".pd-price-seller") or
                           c.css_first(".br-offerSec"))
            merchant = (seller_node.text(strip=True) if seller_node else
                        _merchant_guess(lines))
            rating, reviews = _rating_reviews(text)
            results.append({
                "keyword": self.keyword, "position": pos,
                "product_title": title, "product_image": img,
                "price": float(pm.group(1).replace(",", "")),
                "currency": _currency_from(pm.group(0)),
                "merchant": merchant, "merchant_url": None,
                "product_url": link,
                "rating": rating, "review_count": reviews,
                "shipping_info": ("Free" if "free shipping" in text.lower()
                                  else None),
                "promotion_label": ("SALE" if re.search(
                    r"sale|deal|% off", text, re.I) else None),
            })
        return results


# ==================== 工具函数 ====================
def _norm(s):
    if not s: return ""
    return re.sub(r"\s+", " ", str(s)).strip().lower()[:80]


def _pick_title(lines: list[str]) -> str | None:
    """从一堆行里挑标题：最长的、不含价格、不像评分的。"""
    for ln in sorted(lines, key=len, reverse=True):
        if _PRICE_RE.search(ln):
            continue
        if re.fullmatch(r"[\d.]+\s*\(?\d*\)?\s*", ln or ""):
            continue
        if 5 < len(ln) < 250:
            return ln
    return None


def _first_attr(card, tag: str, attr: str):
    try:
        el = card.css_first(tag)
        return el.attributes.get(attr) if el is not None else None
    except Exception:
        return None


def _merchant_guess(lines: list[str]) -> str | None:
    for ln in lines:
        if 2 < len(ln) < 40 and not _PRICE_RE.search(ln):
            if (".com" in ln or "·" in ln or ln.lower().startswith("by ")
                    or re.match(r"^[A-Z][a-z]+\.[a-z]", ln)):
                return ln.split("·")[0].lstrip("by ").strip()
    return None


def _rating_reviews(text: str):
    m = _RATING_RE.search(text)
    rating = float(m.group(1)) if m else None
    rm = re.search(r"\(\s*([\d,]+)\s*\)|([\d,]+)\s+reviews?", text, re.I)
    reviews = None
    if rm:
        s = rm.group(1) or rm.group(2)
        try: reviews = int(s.replace(",", ""))
        except ValueError: pass
    return rating, reviews


def _currency_from(sym: str) -> str:
    if not sym: return "USD"
    return {"$": "USD", "£": "GBP", "€": "EUR", "¥": "JPY"}.get(
        sym.strip()[0], "USD")


def _clean_google_link(link: str | None) -> str | None:
    """去掉 Google 的 /url?q= 包装。"""
    if not link: return None
    if link.startswith("/url?"):
        try:
            from urllib.parse import parse_qs
            q = parse_qs(link[5:]).get("q") or parse_qs(link[5:]).get("url")
            return q[0] if q else link
        except Exception: return link
    if link.startswith("/aclk?"):
        # Google Shopping 广告链接，转 absolute
        return "https://www.google.com" + link
    return link
