from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ondemand.shopee import ShopeeOnDemand

pytestmark = pytest.mark.unit

FX = Path(__file__).parent / "fixtures" / "ondemand"


def test_parse_item_id_returns_shopid_itemid():
    f = ShopeeOnDemand.parse_item_id
    assert f("https://shopee.com.my/Mouse-i.111.222") == ("111", "222")
    assert f("https://shopee.vn/product/111/222") == ("111", "222")
    with pytest.raises(ValueError):
        f("https://shopee.com.my/shop-page")


def test_parse_listing_maps_required_fields():
    data = json.loads((FX / "shopee_pdp.json").read_text(encoding="utf-8"))
    url = "https://shopee.com.my/Mouse-i.111.222"
    p = ShopeeOnDemand.parse_listing(data, url)

    assert p["sku"] == "111_222"
    assert p["title"] == "Wireless Mouse Ergonomic"
    # Shopee 价格放大 100000 倍
    assert p["sale_price"] == 15.99
    assert p["original_price"] == 29.99
    assert p["currency"] == "VND"
    assert p["site"] == "ondemand_shopee"
    assert p["product_url"] == url
    assert p["ratings"] == 4.7
    assert p["image_urls"] == [
        "https://cf.shopee.com.my/file/abc123",
        "https://cf.shopee.com.my/file/def456",
    ]
    for k in ("sku", "title", "product_url", "site"):
        assert p[k]


def test_parse_reviews_maps_fields():
    data = json.loads((FX / "shopee_ratings.json").read_text(encoding="utf-8"))
    rs = ShopeeOnDemand.parse_reviews(data, ("111", "222"), "https://x")
    assert len(rs) == 2
    assert rs[0]["review_id"] == "555"
    assert rs[0]["platform"] == "ondemand_shopee"
    assert rs[0]["rating"] == 5
    assert rs[0]["content"] == "Works great, very smooth."
    assert rs[0]["reviewer_name"] == "buyer_a"
    assert rs[0]["sku"] == "111_222"
    # ctime(epoch 秒)→ ISO 字符串
    assert rs[0]["review_date"].startswith("2025-") or rs[0]["review_date"].startswith("2026-")
