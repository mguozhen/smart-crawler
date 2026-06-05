from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base

pytestmark = pytest.mark.unit


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return Session()


def test_ondemand_job_model_columns():
    from app.models import OnDemandJob

    s = _session()
    job = OnDemandJob(
        url="https://x/p/MLA1", platform="mercadolibre", kind="product",
        listing_count=1, review_count=4, status="success",
        notes=["ok"], item_skus=["MLA1"],
        workspace_id=1, created_by="tester",
    )
    s.add(job)
    s.commit()
    row = s.query(OnDemandJob).first()
    assert row.url == "https://x/p/MLA1"
    assert row.platform == "mercadolibre"
    assert row.listing_count == 1
    assert row.item_skus == ["MLA1"]
    assert row.notes == ["ok"]
    assert row.workspace_id == 1
    assert row.created_by == "tester"
    assert row.created_at is not None
    s.close()
