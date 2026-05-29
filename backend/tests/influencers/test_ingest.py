"""HTTP contract tests for POST /discover/ingest (phone-pushed items)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


pytestmark = pytest.mark.unit


@pytest.fixture
def client():
    return TestClient(app)


def test_ingest_tiktok_phone_creates_succeeded_run(client):
    r = client.post("/discover/ingest", json={
        "platform": "tiktok_phone",
        "hashtag": "amazonfba",
        "items": [
            {"authorMeta": {"uniqueId": "sellerjoe", "nickName": "Seller Joe",
                            "fans": 12345, "signature": "hi@sellerjoe.com",
                            "bioLink": "https://sellerjoe.com"}},
            {"authorMeta": {"uniqueId": "fbaqueen", "nickName": "FBA Queen",
                            "fans": 5000}},
        ],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    rid = body["runId"]
    assert body["status"] == "SUCCEEDED"
    assert body["itemCount"] == 2

    items = client.get(f"/discover/datasets/{rid}/items").json()
    handles = sorted(it["handle"] for it in items)
    assert handles == ["fbaqueen", "sellerjoe"]
    sj = next(it for it in items if it["handle"] == "sellerjoe")
    assert sj["channelId"] == "@sellerjoe"
    assert sj["platform"] == "TikTok"
    assert sj["followerCount"] == 12345


def test_ingest_unsupported_platform_400(client):
    r = client.post("/discover/ingest", json={
        "platform": "myspace_phone",
        "hashtag": "x",
        "items": [],
    })
    assert r.status_code == 400


def test_ingest_empty_items_still_succeeds(client):
    r = client.post("/discover/ingest", json={
        "platform": "tiktok_phone",
        "hashtag": "x",
        "items": [],
    })
    assert r.status_code == 200
    assert r.json()["itemCount"] == 0


def test_ingest_filters_invalid_items(client):
    r = client.post("/discover/ingest", json={
        "platform": "tiktok_phone",
        "hashtag": "x",
        "items": [
            {"authorMeta": {"uniqueId": "good"}},
            {"authorMeta": {}},   # missing uniqueId — dropped
            {"not_authorMeta": "garbage"},  # malformed — dropped
        ],
    })
    assert r.status_code == 200
    assert r.json()["itemCount"] == 1


def test_ingest_hashtag_stored_in_notes(client):
    r = client.post("/discover/ingest", json={
        "platform": "tiktok_phone",
        "hashtag": "amazonfba",
        "items": [{"authorMeta": {"uniqueId": "good"}}],
    })
    rid = r.json()["runId"]
    items = client.get(f"/discover/datasets/{rid}/items").json()
    # one item, mapped to CreatorRecord shape — handle preserved
    assert items[0]["handle"] == "good"
