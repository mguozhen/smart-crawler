from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ondemand.mercadolibre import MercadoLibreOnDemand

pytestmark = pytest.mark.unit

FX = Path(__file__).parent / "fixtures" / "ondemand"


def test_parse_item_id_from_url():
    f = MercadoLibreOnDemand.parse_item_id
    assert f("https://articulo.mercadolibre.com.mx/MLM-123456789-silla") == "MLM123456789"
    assert f("https://produto.mercadolivre.com.br/MLB-987654321-mesa") == "MLB987654321"
    with pytest.raises(ValueError):
        f("https://articulo.mercadolibre.com.mx/sin-codigo")


def test_parse_listing_maps_required_fields():
    data = json.loads((FX / "ml_item.json").read_text(encoding="utf-8"))
    p = MercadoLibreOnDemand.parse_listing(data, data["permalink"])

    assert p["sku"] == "MLM123456789"
    assert p["title"] == "Silla de Oficina Ergonómica Negra"
    assert p["sale_price"] == 1299.0
    assert p["original_price"] == 1899.0
    assert p["currency"] == "MXN"
    assert p["status"] == "on_sale"
    assert p["site"] == "ondemand_mercadolibre"
    assert p["product_url"] == data["permalink"]
    assert p["image_urls"] == [
        "https://http2.mlstatic.com/D_NQ_123-O.jpg",
        "https://http2.mlstatic.com/D_NQ_456-O.jpg",
    ]
    for k in ("sku", "title", "product_url", "site"):
        assert p[k]


def test_parse_reviews_maps_fields():
    data = json.loads((FX / "ml_reviews.json").read_text(encoding="utf-8"))
    rs = MercadoLibreOnDemand.parse_reviews(data, "MLM123456789",
                                            "https://x/MLM-123456789")
    assert len(rs) == 2
    first = rs[0]
    assert first["review_id"] == "rev-1"
    assert first["platform"] == "ondemand_mercadolibre"
    assert first["site"] == "ondemand_mercadolibre"
    assert first["rating"] == 5
    assert first["title"] == "Excelente"
    assert first["content"] == "Muy cómoda y resistente."
    assert first["sku"] == "MLM123456789"
    assert first["review_date"] == "2026-04-10T12:00:00.000-04:00"
