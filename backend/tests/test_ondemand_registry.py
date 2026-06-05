from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_ondemand_result_accumulates():
    from app.ondemand.base import OnDemandResult

    r = OnDemandResult()
    r.add_listing({"sku": "X1", "title": "Chair", "site": "ondemand_shopee",
                   "product_url": "u"})
    r.add_reviews([{"review_id": "r1"}, {"review_id": "r2"}])
    r.note("done")

    assert len(r.listings) == 1
    assert len(r.reviews) == 2
    assert r.notes == ["done"]
    assert r.summary()["listings"] == 1
    assert r.summary()["reviews"] == 2
