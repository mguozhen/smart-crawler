from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ondemand.lazada import LazadaOnDemand, _clean_review_text, _extract_module_data, _to_float

pytestmark = pytest.mark.unit

FX = Path(__file__).parent / "fixtures" / "ondemand"


def test_clean_review_text_fixes_mojibake_safely():
    # 纯 mojibake emoji 串能被还原(😊 的 utf-8 字节被 latin1 误解码后反解)
    broken = "😊".encode("utf-8").decode("latin1") + "good"
    assert _clean_review_text(broken) == "😊good"
    # 正常 CJK / 重音文字绝不被破坏
    assert _clean_review_text("这把椅子很好") == "这把椅子很好"
    assert _clean_review_text("café résumé") == "café résumé"
    assert _clean_review_text("very comfortable") == "very comfortable"
    assert _clean_review_text(None) is None


def test_parse_item_id_from_url():
    f = LazadaOnDemand.parse_item_id
    # 带 -s<skuId>
    assert f("https://www.lazada.com.my/products/box-i1234567890-s9876543210.html") == "1234567890"
    # 不带 -s(真实形态 pdp-i<id>.html)
    assert f("https://www.lazada.com.my/products/pdp-i149806956.html") == "149806956"
    with pytest.raises(ValueError):
        f("https://www.lazada.com.my/shop/foo/")


def test_to_float_handles_currency_prefix_and_separators():
    assert _to_float("RM114.00") == 114.0
    assert _to_float("$1,299.00") == 1299.0
    assert _to_float("1.234,56") == 1234.56   # 欧式千分位
    assert _to_float(None) is None
    assert _to_float("abc") is None


def test_extract_module_data_balances_braces():
    # 内部含 `};` 的嵌套对象,非贪婪正则会截断,括号配平必须完整提取
    html = 'foo<script>var __moduleData__ = {"a":{"b":"x};y"},"c":1}; more</script>'
    data = _extract_module_data(html)
    assert data == {"a": {"b": "x};y"}, "c": 1}


def test_parse_listing_real_structure():
    data = json.loads((FX / "lazada_pdp_real.json").read_text(encoding="utf-8"))
    url = "https://www.lazada.com.my/products/pdp-i149806956.html"
    p = LazadaOnDemand.parse_listing(data, url)

    assert p["sku"] == "149806956"
    assert p["title"].startswith("F&F: Office Chair Ergonomic")
    assert p["sale_price"] == 114.0
    assert p["original_price"] == 114.0
    assert p["currency"] == "RM"
    assert p["variant_id"] == "175725114"
    assert p["brand"] == "Furniture Farm"
    assert p["site"] == "ondemand_lazada"
    assert p["product_url"] == url
    # 图片:poster 字段、过滤 video、协议补全
    assert len(p["image_urls"]) >= 1
    assert all(u.startswith("https:") for u in p["image_urls"])
    for k in ("sku", "title", "product_url", "site"):
        assert p[k]


def test_parse_reviews_real_structure():
    data = json.loads((FX / "lazada_reviews_real.json").read_text(encoding="utf-8"))
    rs = LazadaOnDemand.parse_reviews(data, "149806956", "https://x")
    assert len(rs) == 2
    r0 = rs[0]
    assert r0["review_id"] == "435680004106956"     # reviewRateId,字符串化
    assert r0["platform"] == "ondemand_lazada"
    assert r0["rating"] == 5
    assert r0["reviewer_name"] == "Jason Yew"
    assert r0["review_date"] == "02 Jul 2025"
    assert "value for money" in r0["content"]
    assert r0["sku"] == "149806956"
