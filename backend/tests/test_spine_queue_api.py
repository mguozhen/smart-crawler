"""Spine 队列 REST/MCP 端到端(mock scrape,不联网)。"""
from unittest.mock import patch

from app.db import SessionLocal, init_db


def _scrape_stub(db, url, **kw):
    return {"scrape_id": "scr_x", "url": url,
            "data": {"title": "MockItem", "confidence": 0.95},
            "metadata": {"canonical": None}, "html": "<html>m</html>",
            "warnings": [], "usage": {"source": "live", "credits_used": 2}}


def test_v2_async_enqueue_and_job_status():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.apikey import generate, hash_key, short
    from app.models import ApiKey
    init_db()
    raw = generate()
    s = SessionLocal()
    try:
        s.add(ApiKey(name="spine-q", key_prefix=short(raw), key_hash=hash_key(raw),
                     scopes=["crawler:scrape", "crawler:read"], active=True))
        s.commit()
    finally:
        s.close()
    headers = {"X-API-Key": raw}
    client = TestClient(app)
    # 清场:避免残留 pending 干扰 claim
    cs = SessionLocal()
    from app.models import SpineJob
    cs.query(SpineJob).filter(SpineJob.status == "pending").delete()
    cs.commit(); cs.close()
    # 入队
    r = client.post("/api/v2/custom/scrape/async", headers=headers,
                    json={"url": "https://x.com/p/9", "dataset": "v2q-set",
                          "entity_type": "product", "save_policy": "main"})
    assert r.status_code == 200, r.text
    jid = r.json()["job_id"]
    assert r.json()["status"] == "pending"
    # 消费(手动 claim+execute,模拟 worker)
    from app.spine_queue import claim_job, execute_job
    assert claim_job("test-worker") == jid
    with patch("app.spine._do_scrape", side_effect=_scrape_stub):
        execute_job(jid)
    # 查状态
    q = client.get(f"/api/v2/custom/job/{jid}", headers=headers)
    assert q.status_code == 200, q.text
    body = q.json()
    assert body["status"] == "success" and body["result_record_id"] is not None


def test_v2_async_requires_auth():
    from fastapi.testclient import TestClient
    from app.main import app
    init_db()
    client = TestClient(app)
    r = client.post("/api/v2/custom/scrape/async",
                    json={"url": "https://x.com", "dataset": "d"})
    assert r.status_code in (401, 403)
