"""精准探针 2 —— sitemap 正常,只让商品页返回 403。

验证 DIRTY 假设:homary/vonhaus 不看 status_code,直接解析商品页 HTML。
若 403 封禁页(含 og:title / 价格样式数字)被解析成 product,即证明会产生脏数据。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 一个商品 URL(让 sitemap 解析出它),和一个 403 商品页
SITEMAP_XML = (
    "<urlset><url><loc>https://example.com/item/fake-chair-12345.html</loc></url>"
    "<url><loc>https://example.com/vh_en/fake-chair</loc></url></urlset>"
)
# 403 封禁页 —— 模拟 PerimeterX/Cloudflare 拦截页,但带 og:title 和价格样
BLOCK_PDP = (
    "<html><head>"
    "<meta property='og:title' content='Access Denied - Security Check'/>"
    "<meta property='og:image' content='https://cdn/block.png'/>"
    "<title>Just a moment...</title></head>"
    "<body><h1>Checking your browser</h1>"
    "<span class='price'>$49.99</span>"
    "<div class='product-price'>$49.99</div></body></html>"
)


class Resp:
    def __init__(self, status, text):
        self.status_code = status
        self.text = text
        self.content = text.encode("utf-8")

    def json(self):
        import json
        raise json.JSONDecodeError("x", self.text, 0)

    def raise_for_status(self):
        if self.status_code >= 400:
            from curl_cffi.requests.exceptions import HTTPError
            raise HTTPError(f"{self.status_code}")


class SmartSession:
    """sitemap URL -> 200 + URL列表; 其它(商品页) -> 403 封禁页。"""
    def __init__(self, *a, **k):
        self.headers = {}
        self.proxies = {}

    def get(self, url, *a, **k):
        if "sitemap" in url or url.endswith(".xml") or url.endswith("/sitemap.xml"):
            return Resp(200, SITEMAP_XML)
        return Resp(403, BLOCK_PDP)        # 商品页一律 403

    def post(self, *a, **k):
        return Resp(403, BLOCK_PDP)


def _make_site(platform, url="https://example.com", country="US"):
    from app.models import Site
    return Site(site=f"probe2_{platform}", brand="Probe", platform=platform,
                url=url, country=country, proxy_tier="none")


if __name__ == "__main__":
    from curl_cffi import requests as creq
    creq.Session = SmartSession
    from app.crawlers.registry import get_crawler

    print("=" * 70)
    print("精准探针 2:sitemap 正常,商品页 403 —— 验证脏解析")
    print("=" * 70)
    import os
    os.environ["HOMARY_LIMIT"] = "2"
    os.environ["VONHAUS_LIMIT"] = "2"
    os.environ["VONHAUS_SCAN_CAP"] = "5"
    for platform in ["nuxt", "vonhaus"]:
        site = _make_site(platform)
        try:
            crawler = get_crawler(site)
            result = crawler.crawl()
            prods = result.products or []
            if prods:
                print(f"[{platform:8}] -> ❌ DIRTY  解析出 {len(prods)} 个商品(应为0)!")
                print(f"            例: {prods[0]}")
            else:
                print(f"[{platform:8}] -> ✅/⚠️ 无脏商品  notes={result.notes}")
        except Exception as e:
            print(f"[{platform:8}] -> RAISED({type(e).__name__}): {str(e)[:80]}")
    print("=" * 70)
