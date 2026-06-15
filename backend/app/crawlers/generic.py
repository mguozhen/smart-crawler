"""通用采集器 —— 覆盖无专用采集器的站点（Flexispot / VonHaus / Woltu / Vidaxl 等）。

策略：sitemap 发现商品 URL → 逐页多策略解析：
  1. JSON-LD <script type="application/ld+json"> 的 Product schema
  2. OpenGraph + 微数据（og:title / product:price:amount / itemprop="price"）
  3. 站内 dataLayer JSON 兜底

sites.yaml 中该站点可选字段：
  sitemap:        sitemap 入口（默认 {url}/sitemap.xml）
  product_match:  商品 URL 必含子串（如 "/p/"）
  max_products:   单次抓取上限（默认 GENERIC_LIMIT）
"""
from __future__ import annotations

import gzip
import html as html_lib
import json
import os
import re
from urllib.parse import urljoin, urlparse

from curl_cffi import requests as creq
from selectolax.parser import HTMLParser

from ..config import get_sites
from ..fetching import CrawlerFetcher, FetchContext
from .base import BaseCrawler, CrawlResult

DEFAULT_LIMIT = int(os.environ.get("GENERIC_LIMIT", "200"))
_CURRENCY = {"US": "USD", "UK": "GBP", "CA": "CAD", "IE": "EUR", "DE": "EUR",
             "IT": "EUR", "ES": "EUR", "FR": "EUR", "RO": "RON", "PT": "EUR",
             "NL": "EUR", "PL": "PLN"}
_PRICE_RE = re.compile(r"[\d.,]+")
_LD_RE = re.compile(
    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_SITEMAP_RE = re.compile(r"(?im)^\s*sitemap:\s*(\S+)\s*$")
_NOISE_RE = re.compile(
    r"(blog|/article|/news|care-center|/category/|/help|/about|/contact|"
    r"/privacy|/terms|/login|/account|/cart|/checkout|/search|/stores?|"
    r"/collections?$|/categories?$)",
    re.I,
)
_PRODUCT_HINT_RE = re.compile(
    r"(/products?/|/product/|/p/|/pd/|/pdp/|/item/|/itm/|/dp/|"
    r"/sku/|/catalog/product|product[-_]|p-[0-9]|sku[-_/]?[0-9])",
    re.I,
)


class GenericCrawler(BaseCrawler):
    platform = "generic"

    def __init__(self, site):
        super().__init__(site)
        hints = next((c for c in get_sites() if c["site"] == site.site), {})
        self.base = site.url.rstrip("/")
        self.sitemap_hint = hints.get("sitemap")
        self.sitemap = self.sitemap_hint or (self.base + "/sitemap.xml")
        self.product_match = hints.get("product_match", "")
        self.exclude_match = hints.get("exclude_match", "")
        self.limit = self._resolve_limit(DEFAULT_LIMIT)

    def _session(self) -> creq.Session:
        s = creq.Session(impersonate="chrome")
        s.headers.update({"User-Agent": self.ua(),
                          "Accept-Language": "en-US,en;q=0.9"})
        if self.proxy:
            s.proxies = {"http": self.proxy, "https": self.proxy}
        return s

    def _fetcher(self, kind: str, source: str) -> CrawlerFetcher:
        return self.make_fetcher(kind=kind, source=source,
                                 timeout=30, use_proxy=True)

    def _fetch_text(self, sess: creq.Session | None, url: str,
                    *, kind: str, source: str) -> tuple[int | None, str, bytes]:
        if sess is None:
            res = self._fetcher(kind, source).get(
                url,
                headers={"Accept-Language": "en-US,en;q=0.9"},
            )
            return res.status, res.text, res.content
        try:
            resp = sess.get(url, timeout=30)
            return resp.status_code, resp.text or "", resp.content or b""
        except Exception:
            return None, "", b""

    def _discover_sitemaps(self, sess: creq.Session | None) -> list[str]:
        """从配置、robots.txt 和常见路径发现 sitemap 入口。"""
        urls: list[str] = []
        if self.sitemap_hint:
            urls.append(self.sitemap_hint)

        robots = urljoin(self.base + "/", "robots.txt")
        try:
            status, text, _ = self._fetch_text(
                sess, robots, kind="sitemap", source="robots")
            if status == 200:
                urls.extend(_SITEMAP_RE.findall(text or ""))
        except Exception:
            pass

        for path in (
            "sitemap.xml",
            "sitemap_index.xml",
            "sitemap-index.xml",
            "sitemap/sitemap.xml",
            "sitemaps/sitemap.xml",
            "product-sitemap.xml",
            "products-sitemap.xml",
            "sitemap-products.xml",
        ):
            urls.append(urljoin(self.base + "/", path))

        return self._dedupe(urls)

    def _sitemap_locs(self, sess: creq.Session | None, url: str,
                      depth: int = 0) -> list[str]:
        """递归展开 sitemap（索引 / .gz / 普通），返回全部 <loc>。"""
        if depth > 3:
            return []
        try:
            status, _, raw = self._fetch_text(
                sess, url, kind="sitemap", source="sitemap")
            if status is None or status >= 400:
                return []
        except Exception:
            return []
        try:
            text = (gzip.decompress(raw) if url.endswith(".gz")
                    else raw).decode("utf-8", "ignore")
        except (OSError, gzip.BadGzipFile):
            text = raw.decode("utf-8", "ignore")
        locs = [html_lib.unescape(x.strip())
                for x in re.findall(r"<loc>\s*(.*?)\s*</loc>", text)]
        sub = [l for l in locs if l.endswith(".xml") or l.endswith(".xml.gz")]
        if sub and len(sub) == len(locs):            # 纯 sitemap 索引，递归
            out: list[str] = []
            for s in sub[:12]:
                out.extend(self._sitemap_locs(sess, s, depth + 1))
            return out
        return locs

    def _discover_product_urls(self, sess: creq.Session | None,
                               result: CrawlResult) -> list[str]:
        locs: list[str] = []
        sitemap_urls = self._discover_sitemaps(sess)
        for sm in sitemap_urls[:16]:
            before = len(locs)
            locs.extend(self._sitemap_locs(sess, sm))
            if len(locs) > before:
                result.notes.append(f"sitemap 命中: {sm}")

        cands = [u for u in self._dedupe(locs) if self._is_candidate_url(u)]
        products = [u for u in cands if self._is_product_url(u)]
        if not products and not self.product_match:
            products = cands

        if products:
            return products

        links = self._links_from_page(sess, self.site.url)
        if links:
            result.notes.append(f"入口页发现 {len(links)} 个候选商品链接")
        return links

    def _links_from_page(self, sess: creq.Session | None, url: str) -> list[str]:
        status, text, _ = self._fetch_text(
            sess, url, kind="category", source="homepage")
        if status is None or status >= 400:
            return []
        base_host = urlparse(self.base).netloc
        tree = HTMLParser(text or "")
        links: list[str] = []
        for node in tree.css("a[href]"):
            href = node.attributes.get("href") or ""
            full = urljoin(url, href.split("#", 1)[0])
            if urlparse(full).netloc != base_host:
                continue
            if self._is_candidate_url(full) and self._is_product_url(full):
                links.append(full)
            if len(links) >= self.limit:
                break
        return self._dedupe(links)

    def crawl(self) -> CrawlResult:
        result = CrawlResult()
        sess = None

        if self.site.platform and self.site.platform != self.platform:
            result.notes.append(
                f"未注册平台 {self.site.platform}，已自动降级为 generic 通用抓取")

        products = self._discover_product_urls(sess, result)
        total = len(products)
        targets = products[: self.limit]
        result.notes.append(
            f"通用发现 {total} 个候选商品 URL，本次抓取 {len(targets)} 条")
        if not targets:
            result.notes.append("⚠ 通用发现未找到商品 URL，可为该站点配置 "
                                 "sitemap / product_match，或启用专用/浏览器策略")
            return result

        ok = 0
        for url in targets:
            try:
                row = self._parse(sess, url)
                if row:
                    result.products.append(row)
                    ok += 1
            except Exception as exc:
                result.notes.append(f"跳过 {url[:60]}: {exc}")
            self.sleep()
        result.notes.append(f"成功解析 {ok}/{len(targets)} 个商品页")
        return result

    def _is_candidate_url(self, url: str) -> bool:
        if not url or url.endswith((".xml", ".xml.gz")):
            return False
        if self.exclude_match and self.exclude_match in url:
            return False
        path = urlparse(url).path.lower()
        if not path or path == "/":
            return False
        if _NOISE_RE.search(path):
            return False
        return True

    def _is_product_url(self, url: str) -> bool:
        if self.product_match:
            return self.product_match in url
        path = urlparse(url).path.lower()
        return bool(_PRODUCT_HINT_RE.search(path))

    def _parse(self, sess: creq.Session | None, url: str) -> dict | None:
        status, html, _ = self._fetch_text(
            sess, url, kind="product", source="candidate")
        if status is None or status >= 400:
            return None
        self.snapshot(self._slug(url), html)       # 原始商品页归档
        tree = HTMLParser(html)
        data = self._from_jsonld(html) or {}

        title = data.get("name") or self._meta(tree, "og:title")
        if not title:
            return None
        sale = data.get("price") or self._meta_price(tree)
        if sale is None:
            return None
        original = data.get("original_price") or sale

        return {
            "sku": data.get("sku") or self._slug(url),
            "spu": data.get("sku") or self._slug(url),
            "title": (title or "").strip(),
            "description": data.get("description")
            or self._meta(tree, "og:description"),
            "image_urls": data.get("images")
            or ([self._meta(tree, "og:image")] if self._meta(tree, "og:image") else []),
            "category_path": data.get("category"),
            "sale_price": sale,
            "original_price": original,
            "currency": data.get("currency")
            or _CURRENCY.get(self.site.country, "USD"),
            "ratings": data.get("rating"),
            "review_count": data.get("review_count"),
            "status": data.get("status", "on_sale"),
            "has_video": "<video" in html,
            "mpn": data.get("mpn"),
            "gtin": data.get("gtin"),
            "brand": data.get("brand") or self.site.brand,
            "product_url": url,
            "site": self.site.site,
        }

    @staticmethod
    def _from_jsonld(html: str) -> dict | None:
        """解析 JSON-LD 的 Product schema。"""
        for block in _LD_RE.findall(html):
            try:
                doc = json.loads(block.strip())
            except json.JSONDecodeError:
                continue
            for it in (doc if isinstance(doc, list) else
                       doc.get("@graph", [doc]) if isinstance(doc, dict) else []):
                if not isinstance(it, dict):
                    continue
                t = it.get("@type")
                is_product = t == "Product" or (
                    isinstance(t, list) and "Product" in t)
                if not is_product:
                    continue
                offers = it.get("offers") or {}
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                rating = it.get("aggregateRating") or {}
                brand = it.get("brand")
                if isinstance(brand, dict):
                    brand = brand.get("name")
                avail = str(offers.get("availability", "")).lower()
                imgs = it.get("image")
                if isinstance(imgs, str):
                    imgs = [imgs]
                return {
                    "name": it.get("name"),
                    "sku": it.get("sku") or it.get("mpn"),
                    "description": it.get("description"),
                    "images": imgs or [],
                    "price": GenericCrawler._num(offers.get("price")),
                    "currency": offers.get("priceCurrency"),
                    "status": "out_of_stock" if "outofstock" in avail
                    or "soldout" in avail else "on_sale",
                    "rating": GenericCrawler._num(rating.get("ratingValue")),
                    "review_count": GenericCrawler._int(rating.get("reviewCount")),
                    "mpn": it.get("mpn"),
                    "gtin": it.get("gtin13") or it.get("gtin"),
                    "brand": brand,
                }
        return None

    @staticmethod
    def _meta(tree: HTMLParser, prop: str) -> str | None:
        node = (tree.css_first(f'meta[property="{prop}"]')
                or tree.css_first(f'meta[name="{prop}"]'))
        return node.attributes.get("content") if node else None

    def _meta_price(self, tree: HTMLParser):
        for sel in ('meta[property="product:price:amount"]',
                    'meta[property="og:price:amount"]',
                    '[itemprop="price"]'):
            node = tree.css_first(sel)
            if node:
                val = node.attributes.get("content") or node.text(strip=True)
                p = self._num(val)
                if p:
                    return p
        return None

    @staticmethod
    def _slug(url: str) -> str:
        return url.rstrip("/").split("/")[-1].split("?")[0][:80]

    @staticmethod
    def _dedupe(items: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in items:
            if item and item not in seen:
                seen.add(item)
                out.append(item)
        return out

    @staticmethod
    def _num(v):
        if v is None:
            return None
        m = _PRICE_RE.search(str(v).replace(",", "."))
        if not m:
            return None
        try:
            return float(m.group())
        except ValueError:
            return None

    @staticmethod
    def _int(v):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None
