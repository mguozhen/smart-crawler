"""HTTP contract tests for /discover/* — uses FastAPI TestClient + monkeypatched adapters."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.main import app


pytestmark = pytest.mark.unit


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(
        "app.influencers.yt_about.fetch_about",
        lambda url, timeout=20: {"email": "x@x.com", "websiteUrl": "https://x.com"},
    )
    return TestClient(app)


def _await_terminal(client, rid, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/discover/runs/{rid}")
        assert r.status_code == 200
        status = r.json()["status"]
        if status in ("SUCCEEDED", "FAILED"):
            return r.json()
        time.sleep(0.05)
    raise AssertionError("run did not reach terminal state in time")


def test_yt_about_run_end_to_end(client):
    r = client.post("/discover/runs", json={
        "platform": "youtube_about",
        "urls": ["https://www.youtube.com/@a/about"],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    rid = body["runId"]
    assert body["status"] == "PENDING"
    assert body["datasetId"] == rid

    final = _await_terminal(client, rid)
    assert final["status"] == "SUCCEEDED"
    assert final["itemCount"] == 1

    items = client.get(f"/discover/datasets/{rid}/items").json()
    assert items == [{"email": "x@x.com", "websiteUrl": "https://x.com"}]


def test_unknown_platform_returns_400(client):
    r = client.post("/discover/runs", json={"platform": "myspace", "urls": []})
    assert r.status_code == 400
    assert "unknown platform" in r.json()["detail"]


def test_get_unknown_run_returns_404(client):
    r = client.get("/discover/runs/does-not-exist")
    assert r.status_code == 404


def test_items_pagination(client, monkeypatch):
    monkeypatch.setattr(
        "app.influencers.yt_about.fetch_about",
        lambda url, timeout=20: {"email": url[-5:], "websiteUrl": None},
    )
    urls = [f"https://www.youtube.com/@a{i}/about" for i in range(5)]
    rid = client.post("/discover/runs", json={
        "platform": "youtube_about", "urls": urls,
    }).json()["runId"]
    _await_terminal(client, rid)
    items = client.get(f"/discover/datasets/{rid}/items?limit=2&offset=1").json()
    assert len(items) == 2
