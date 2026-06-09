"""封禁处理探针 —— 给爬虫喂 403,观察实际行为(验证用,非回归断言)。

运行:  ../.venv/bin/python -m pytest tests/test_ban_handling_probe.py -s -m unit
或直接: ../.venv/bin/python tests/test_ban_handling_probe.py

三种结局:
  PROTECTED  抛 BlockedError          —— ✅ 会触发站点冷却
  SILENT     返回空 result,不抛错     —— ⚠️ worker 会反复重打
  DIRTY      返回了 products          —— ❌ 把封禁页解析成脏数据
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.antiban import BlockedError

# ---- 假的 403 响应 / session,拦截所有出网 ----
BLOCK_BODY = (
    "<html><head><title>Access Denied</title></head>"
    "<body><h1>403 Forbidden</h1>"
    # 故意塞一个像价格的数字,看脏解析会不会把它当商品价
    "<span class='price'>$999.99</span>"
    "<meta property='og:title' content='Access Denied'/></body></html>"
)


class Fake403Response:
    status_code = 403
    text = BLOCK_BODY
    content = BLOCK_BODY.encode("utf-8")

    def json(self):
        import json
        raise json.JSONDecodeError("blocked", BLOCK_BODY, 0)

    def raise_for_status(self):
        from curl_cffi.requests.exceptions import HTTPError
        raise HTTPError("403 Forbidden")


class Fake403Session:
    def __init__(self, *a, **k):
        self.headers = {}
        self.proxies = {}

    def get(self, *a, **k):
        return Fake403Response()

    def post(self, *a, **k):
        return Fake403Response()

    def update(self, *a, **k):
        pass


def _make_site(platform, url="https://example.com", country="US"):
    from app.models import Site
    return Site(site=f"probe_{platform}", brand="Probe", platform=platform,
                url=url, country=country, proxy_tier="none")


def _classify(fn):
    """跑 crawl,把结果归类成 PROTECTED / SILENT / DIRTY / ERROR。"""
    try:
        result = fn()
    except BlockedError as e:
        return "PROTECTED", f"BlockedError: {e}"
    except Exception as e:                       # 其它异常也算"没专门处理封禁"
        return f"RAISED({type(e).__name__})", str(e)[:80]
    n = len(getattr(result, "products", []) or [])
    if n == 0:
        return "SILENT", f"notes={getattr(result,'notes',[])}"
    return "DIRTY", f"{n} products,例: {result.products[0]}"


# 平台 -> 构造爬虫的方式
CASES = ["nuxt", "vonhaus", "generic", "magento"]


@pytest.mark.unit
@pytest.mark.parametrize("platform", CASES)
def test_probe(platform, monkeypatch, capsys):
    from curl_cffi import requests as creq
    monkeypatch.setattr(creq, "Session", Fake403Session)

    from app.crawlers.registry import get_crawler
    site = _make_site(platform)
    crawler = get_crawler(site)

    verdict, detail = _classify(crawler.crawl)
    with capsys.disabled():
        print(f"\n[{platform:10}] -> {verdict:18} {detail}")


if __name__ == "__main__":
    # 直接运行模式:不依赖 pytest fixture,手动 patch
    from curl_cffi import requests as creq
    creq.Session = Fake403Session
    from app.crawlers.registry import get_crawler

    print("=" * 70)
    print("封禁处理探针:给 4 个爬虫喂 403,观察行为")
    print("=" * 70)
    for platform in CASES:
        site = _make_site(platform)
        try:
            crawler = get_crawler(site)
            verdict, detail = _classify(crawler.crawl)
        except Exception as e:
            verdict, detail = f"SETUP_FAIL({type(e).__name__})", str(e)[:80]
        print(f"[{platform:10}] -> {verdict:18} {detail}")
    print("=" * 70)
    print("PROTECTED=✅有保护  SILENT=⚠️静默吞封禁  DIRTY=❌解析脏数据")
