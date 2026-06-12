"""Spine 异步队列测试。"""
from datetime import datetime

from sqlalchemy import inspect

from app.db import engine, init_db


def test_spine_jobs_table_exists():
    init_db()
    insp = inspect(engine)
    assert insp.has_table("spine_jobs"), "缺表 spine_jobs"
    cols = {c["name"] for c in insp.get_columns("spine_jobs")}
    for c in ("id", "url", "dataset", "entity_type", "save_policy",
              "force_live", "status", "retries", "max_retries",
              "next_attempt_at", "worker", "result_record_id", "error",
              "workspace_id", "created_at", "started_at", "finished_at"):
        assert c in cols, f"spine_jobs 缺列 {c}"


from app.db import SessionLocal


def _clear_pending():
    """清空残留 pending,保证 claim/run_loop 测试领到的是本测试入队的 job。

    队列 claim 是全局领最旧到期 pending;测试共享文件 DB,故 claim/loop 类
    测试入队前必须清场,否则会领到别的测试残留的 job。
    """
    from app.models import SpineJob
    s = SessionLocal()
    s.query(SpineJob).filter(SpineJob.status == "pending").delete()
    s.commit(); s.close()


def test_enqueue_creates_pending_job():
    init_db(); s = SessionLocal()
    from app.spine_queue import enqueue
    jid = enqueue(s, "https://x.com/p/1", "q-set", entity_type="product",
                  workspace_id=None)
    s.commit()
    from app.models import SpineJob
    job = s.get(SpineJob, jid)
    assert job.status == "pending" and job.url == "https://x.com/p/1"
    assert job.dataset == "q-set" and job.retries == 0 and job.max_retries == 3
    assert job.next_attempt_at is not None
    s.close()


def test_claim_job_optimistic_lock_single_winner():
    init_db()
    _clear_pending()  # 清场,保证 claim 领到本测试入队的那条
    from app.models import SpineJob
    s = SessionLocal()
    from app.spine_queue import enqueue, claim_job
    jid = enqueue(s, "https://x.com/p/2", "claim-set", workspace_id=None)
    s.commit(); s.close()
    # 两个 worker 抢同一个最旧 job:只有一个领到
    first = claim_job("worker-A")
    second = claim_job("worker-B")
    assert first == jid
    assert second is None  # 已被领走,无其他 pending
    s2 = SessionLocal()
    job = s2.get(SpineJob, jid)
    assert job.status == "running" and job.worker == "worker-A"
    s2.close()


def test_claim_job_empty_returns_none():
    init_db()
    _clear_pending()  # 清场后无 pending
    from app.spine_queue import claim_job
    assert claim_job("worker-X") is None


from unittest.mock import patch

from app.models import SpineJob


def _scrape_stub(db, url, **kw):
    return {"scrape_id": "scr_q", "url": url,
            "data": {"title": "QueuedItem", "confidence": 0.95},
            "metadata": {"canonical": None}, "html": "<html>q</html>",
            "warnings": [], "usage": {"source": "live", "credits_used": 2}}


def test_execute_job_success_sets_record_id():
    init_db(); s = SessionLocal()
    from app.spine_queue import enqueue, claim_job, execute_job
    jid = enqueue(s, "https://x.com/p/ok", "exec-set", entity_type="product",
                  save_policy="main", workspace_id=None)
    s.commit(); s.close()
    assert claim_job("w1") == jid
    with patch("app.spine._do_scrape", side_effect=_scrape_stub):
        out = execute_job(jid)
    assert out["status"] == "success"
    s2 = SessionLocal()
    job = s2.get(SpineJob, jid)
    assert job.status == "success" and job.result_record_id is not None
    assert job.finished_at is not None
    s2.close()


def test_execute_job_failure_retries_with_backoff():
    init_db(); s = SessionLocal()
    from app.spine_queue import enqueue, claim_job, execute_job
    jid = enqueue(s, "https://x.com/p/fail", "fail-set", workspace_id=None)
    s.commit(); s.close()
    claim_job("w1")
    def boom(db, url, **kw):
        raise RuntimeError("scrape exploded")
    with patch("app.spine._do_scrape", side_effect=boom):
        out = execute_job(jid)
    assert out["status"] == "pending"  # 还能重试 → 回 pending
    s2 = SessionLocal()
    job = s2.get(SpineJob, jid)
    assert job.status == "pending" and job.retries == 1
    assert job.next_attempt_at > datetime.utcnow()  # 退避到未来
    s2.close()


def test_execute_job_exhausts_retries_to_failed():
    init_db(); s = SessionLocal()
    from app.spine_queue import enqueue, claim_job, execute_job
    jid = enqueue(s, "https://x.com/p/dead", "dead-set", max_retries=1,
                  workspace_id=None)
    s.commit(); s.close()
    def boom(db, url, **kw):
        raise RuntimeError("always fails")
    claim_job("w1")
    with patch("app.spine._do_scrape", side_effect=boom):
        execute_job(jid)  # retries 0→1
    # 第 1 次后 retries=1 == max_retries=1 → 直接 failed(不再回 pending)
    s2 = SessionLocal()
    job = s2.get(SpineJob, jid)
    assert job.status == "failed" and job.retries == 1
    assert "always fails" in (job.error or "")
    s2.close()


def test_claim_skips_jobs_in_backoff_window():
    init_db()
    _clear_pending()  # 清场,保证 claim 只可能领到本测试入队的 job
    s = SessionLocal()
    from app.spine_queue import enqueue, claim_job, execute_job
    jid = enqueue(s, "https://x.com/p/backoff", "bo-set", workspace_id=None)
    s.commit(); s.close()
    claim_job("w1")
    def boom(db, url, **kw):
        raise RuntimeError("fail once")
    with patch("app.spine._do_scrape", side_effect=boom):
        execute_job(jid)  # → pending,next_attempt_at = now + 30s
    # 退避窗口内,claim 领不到
    assert claim_job("w2") is None
    s2 = SessionLocal()
    job = s2.get(SpineJob, jid)
    assert job.status == "pending" and job.next_attempt_at > datetime.utcnow()
    s2.close()
