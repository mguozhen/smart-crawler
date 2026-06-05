from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ondemand.lazada import LazadaOnDemand

pytestmark = pytest.mark.unit

FX = Path(__file__).parent / "fixtures" / "ondemand"


def test_parse_item_id_from_url():
    f = LazadaOnDemand.parse_item_id
    assert f("https://www.lazada.com.my/products/box-i1234567890-s9876543210.html") == "1234567890"
    with pytest.raises(ValueError):
        f("https://www.lazada.com.my/shop/foo/")


def test_parse_listing_maps_required_fields():
    data = json.loads((FX / "lazada_pdp.json").read_text(encoding="utf-8"))
    url = "https://www.lazada.com.my/products/box-i1234567890-s9876543210.html"
    p = LazadaOnDemand.parse_listing(data, url)

    assert p["sku"] == "1234567890"
    assert p["title"] == "Foldable Storage Box 3-Tier"
    assert p["sale_price"] == 39.90
    assert p["original_price"] == 59.90
    assert p["currency"] == "MYR"
    assert p["site"] == "ondemand_lazada"
    assert p["product_url"] == url
    assert p["image_urls"][0] == "https://img.lazcdn.com/a.jpg"
    assert p["status"] == "on_sale"
    for k in ("sku", "title", "product_url", "site"):
        assert p[k]


def test_parse_reviews_maps_fields():
    data = json.loads((FX / "lazada_reviews.json").read_text(encoding="utf-8"))
    rs = LazadaOnDemand.parse_reviews(data, "1234567890", "https://x")
    assert len(rs) == 2
    assert rs[0]["review_id"] == "L-rev-1"
    assert rs[0]["platform"] == "ondemand_lazada"
    assert rs[0]["rating"] == 5
    assert rs[0]["content"] == "Good quality, fast delivery."
    assert rs[0]["reviewer_name"] == "Ali"
    assert rs[0]["review_date"] == "10 Apr 2026"
    assert rs[0]["sku"] == "1234567890"
