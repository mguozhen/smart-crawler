from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import billing
from app.db import Base
from app.models import Usage

pytestmark = pytest.mark.unit


@pytest.fixture
def mem_session(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(billing, "SessionLocal", Session)
    return Session


def test_record_usage_persists_counts(mem_session):
    billing.record_usage(
        api_key_id=None, endpoint="/crawl/job", record_count=10,
        bytes_returned=0, duration_ms=1200, credits_used=10,
        workspace_id=7, api_calls=4, browser_opens=1, pages_fetched=5,
    )
    with mem_session() as s:
        row = s.query(Usage).first()
        assert row.workspace_id == 7
        assert row.api_calls == 4
        assert row.browser_opens == 1
        assert row.pages_fetched == 5
        assert row.credits_used == 10


def test_record_usage_counts_default_zero(mem_session):
    billing.record_usage(
        api_key_id=None, endpoint="/x", record_count=1,
        bytes_returned=0, duration_ms=0,
    )
    with mem_session() as s:
        row = s.query(Usage).first()
        assert row.api_calls == 0
        assert row.pages_fetched == 0
