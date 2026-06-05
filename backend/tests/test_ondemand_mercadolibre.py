from __future__ import annotations

from pathlib import Path

import pytest

from app.ondemand.mercadolibre import MercadoLibreOnDemand, _ld_product

pytestmark = pytest.mark.unit

FX = Path(__file__).parent / "fixtures" / "ondemand"
_HTML = (FX / "ml_pdp_real.html").read_text(encoding="utf-8")


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


def test_parse_listing_raises_on_shell_page():
    # 没有 JSON-LD Product 的壳页 -> BlockedError(交给 runner 切代理重试)
    from app.antiban import BlockedError
    with pytest.raises(BlockedError):
        MercadoLibreOnDemand.parse_listing("<html><body>shell</body></html>", "u")


def test_parse_reviews_from_dom():
    rs = MercadoLibreOnDemand.parse_reviews(_HTML, "MLA62019558",
                                            "https://x/p/MLA62019558")
    assert len(rs) == 2
    r0 = rs[0]
    assert r0["review_id"] == "MLA62019558_1"
    assert r0["platform"] == "ondemand_mercadolibre"
    assert r0["rating"] == 5
    assert "Muy lindas" in r0["content"]
    assert r0["sku"] == "MLA62019558"
    # 第二条 2 星
    assert rs[1]["rating"] == 2
    assert "se rompió" in rs[1]["content"]
