from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Product
from app.pipeline import normalize
from app.runner import _detect_promotions

pytestmark = pytest.mark.unit


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return Session()


def test_normalize_keeps_original_none_when_missing():
    p = normalize({"sku": "S1", "title": "t", "product_url": "u",
                   "site": "x", "sale_price": 10})
    assert p["original_price"] is None


def test_normalize_preserves_real_original():
    p = normalize({"sku": "S1", "title": "t", "product_url": "u",
                   "site": "x", "sale_price": 10, "original_price": 20})
    assert p["original_price"] == 20.0


def test_detect_promotions_only_fires_on_real_discount():
    db = _session()
    # A: 有真实折扣 original 20 > sale 10
    db.add(Product(site="x", sku="A", title="A", sale_price=10.0,
                   original_price=20.0, status="on_sale"))
    # B: 仅 sale，无 original（回填已删 → original 应为 None，不算促销）
    db.add(Product(site="x", sku="B", title="B", sale_price=10.0,
                   original_price=None, status="on_sale"))
    db.commit()
    n = _detect_promotions(db, "x")
    db.flush()  # _detect_promotions adds via session.add(); flush makes them visible to query
    assert n == 1
    from app.models import Promotion
    skus = [r.sku for r in db.query(Promotion).filter(Promotion.site == "x").all()]
    assert skus == ["A"]
