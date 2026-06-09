from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ondemand.mercadolibre import (
    MercadoLibreOnDemand, _country_for, _ld_product)

pytestmark = pytest.mark.unit

FX = Path(__file__).parent / "fixtures" / "ondemand"
_HTML = (FX / "ml_pdp_real.html").read_text(encoding="utf-8")
_HTML_BR = (FX / "ml_pdp_br_real.html").read_text(encoding="utf-8")
_REVIEWS_JSON = json.loads(
    (FX / "ml_reviews_real.json").read_text(encoding="utf-8"))


def test_parse_item_id_from_url():
    f = MercadoLibreOnDemand.parse_item_id
    assert f("https://articulo.mercadolibre.com.mx/MLM-123456789-silla") == "MLM123456789"
    assert f("https://produto.mercadolivre.com.br/MLB-987654321-mesa") == "MLB987654321"
    # catalog /p/MLA…
    assert f("https://www.mercadolibre.com.ar/x/p/MLA62019558") == "MLA62019558"
    with pytest.raises(ValueError):
        f("https://articulo.mercadolibre.com.mx/sin-codigo")


def test_ld_product_picks_product_block():
    d = _ld_product(_HTML)
    assert d is not None
    assert d["@type"] == "Product"
    assert d["sku"] == "MLA62019558"


def test_parse_listing_from_jsonld():
    url = "https://www.mercadolibre.com.ar/x/p/MLA62019558"
    p = MercadoLibreOnDemand.parse_listing(_HTML, url)

    assert p["sku"] == "MLA62019558"
    assert p["title"].startswith("Pack x6 Sillas Tulip")
    assert p["sale_price"] == 208739
    assert p["currency"] == "ARS"
    assert p["status"] == "on_sale"
    assert p["brand"] == "MAS QUE MUEBLES"
    assert p["ratings"] == 4.6
    assert p["review_count"] == 442
    assert p["image_urls"] == [
        "https://http2.mlstatic.com/D_NQ_NP_843344-MLA97429097480_112025-O.webp"]
    assert p["site"] == "ondemand_mercadolibre"
    assert p["product_url"] == url
    for k in ("sku", "title", "product_url", "site"):
        assert p[k]


def test_country_derived_from_domain_cctld():
    # locale 指纹必须随站点 ccTLD 走,否则反爬易弹验证壳页
    f = _country_for
    assert f("https://produto.mercadolivre.com.br/MLB-123-x") == "BR"
    assert f("https://articulo.mercadolibre.com.mx/MLM-123-x") == "MX"
    assert f("https://www.mercadolibre.com.ar/x/p/MLA62019558") == "AR"
    assert f("https://www.mercadolibre.cl/MLC-1-x") == "CL"
    # 未知/无 ccTLD -> 退回流量最大的 BR
    assert f("https://example.com/x") == "BR"
    assert f("not a url") == "BR"


def test_parse_listing_raises_on_shell_page():
    # 没有 JSON-LD Product 的壳页 -> BlockedError(交给 runner 切代理重试)
    from app.antiban import BlockedError
    with pytest.raises(BlockedError):
        MercadoLibreOnDemand.parse_listing("<html><body>shell</body></html>", "u")


def test_parse_reviews_from_json():
    # 数据源 = noindex/catalog/reviews/{id}/search 的 JSON,带真实数字 review id
    rs = MercadoLibreOnDemand.parse_reviews(
        _REVIEWS_JSON, "MLB3856668644", "https://x/MLB-3856668644")
    assert len(rs) == 2
    r0 = rs[0]
    # 用真实数字 id 作 review_id(替掉旧的 sku_序号 合成键),去重才稳
    assert r0["review_id"] == "989655573"
    assert r0["platform"] == "ondemand_mercadolibre"
    assert r0["site"] == "ondemand_mercadolibre"
    assert r0["rating"] == 4
    assert "Achei mediana" in r0["content"]
    assert r0["sku"] == "MLB3856668644"
    assert r0["product_url"] == "https://x/MLB-3856668644"
    # 相对日期原样保留(接口不给绝对时间)
    assert r0["review_date"] == "Há mais de 1 ano"
    # 不同星级,证明非写死
    assert rs[1]["review_id"] == "762755857"
    assert rs[1]["rating"] == 1
    assert "não gostei" in rs[1]["content"]


def test_parse_reviews_ignores_empty_or_malformed():
    # 缺正文 / 缺 id 的条目跳过,不污染入库
    data = {"reviews": [
        {"id": 1, "rating": 5, "comment": {"content": {"text": ""}}},   # 空正文
        {"rating": 5, "comment": {"content": {"text": "x"}}},            # 缺 id
        {"id": 2, "rating": 3, "comment": {"content": {"text": "ok"}}},  # 有效
    ]}
    rs = MercadoLibreOnDemand.parse_reviews(data, "MLB1", "u")
    assert [r["review_id"] for r in rs] == ["2"]


def test_parse_listing_brazil_brl():
    p = MercadoLibreOnDemand.parse_listing(_HTML_BR, "https://x/MLB-1")
    assert p["currency"] == "BRL"
    assert p["sale_price"] == 26.99
    assert p["ratings"] == 4.6
    assert p["review_count"] == 7966
