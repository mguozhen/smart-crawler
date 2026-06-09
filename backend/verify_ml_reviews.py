#!/usr/bin/env python
"""本地验证美客多评论全量抓取 —— 纯 HTTP(curl_cffi),不碰浏览器/代理/数据库。

用法:
    .venv-verify/bin/python verify_ml_reviews.py <商品URL> [上限条数]

示例:
    .venv-verify/bin/python verify_ml_reviews.py \
        "https://produto.mercadolivre.com.br/MLB-3856668644-manta" 2000
"""
import sys
from collections import Counter

# 直接按文件加载 mercadolibre,绕开 app/ondemand/__init__.py(它会拉 runner→db→
# sqlalchemy/yaml 等重依赖)。本地验证评论抓取只需 curl_cffi,保持环境最小。
import importlib.util
import pathlib

_HERE = pathlib.Path(__file__).resolve().parent
_PKG = _HERE / "app" / "ondemand"


def _load(name, path, pkg=None):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# antiban / base 是 mercadolibre 的相对依赖,先以正确的包名预加载
import types
_app = types.ModuleType("app"); _app.__path__ = [str(_HERE / "app")]; sys.modules["app"] = _app
_ond = types.ModuleType("app.ondemand"); _ond.__path__ = [str(_PKG)]
sys.modules["app.ondemand"] = _ond
_load("app.antiban", _HERE / "app" / "antiban.py")
_load("app.ondemand.base", _PKG / "base.py")
_ml = _load("app.ondemand.mercadolibre", _PKG / "mercadolibre.py")
MercadoLibreOnDemand = _ml.MercadoLibreOnDemand


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    url = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 2000

    c = MercadoLibreOnDemand()
    iid = c.parse_item_id(url)
    print(f"商品ID: {iid} | 评论域名: {c._review_host(url)} | siteId: {c._site_id(iid)}")
    print(f"开始抓取(上限 {limit})…分 5 个星级桶翻页,约需 1-2 分钟\n")

    reviews = c.fetch_reviews(iid, url, limit=limit)

    ids = {r["review_id"] for r in reviews}
    print(f"✓ 抓到 {len(reviews)} 条 | 去重后 {len(ids)} 条唯一")
    print(f"  星级分布: {dict(sorted(Counter(r['rating'] for r in reviews).items()))}")
    print(f"  全部真实数字ID: {all(r['review_id'].isdigit() for r in reviews)}")
    print(f"  全部有正文:     {all(r['content'] for r in reviews)}")
    print("\n  前 3 条样例:")
    for r in reviews[:3]:
        print(f"    [{r['rating']}★] id={r['review_id']} {r['review_date']}")
        print(f"         {r['content'][:70]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
