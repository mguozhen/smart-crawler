"""回归:enqueue 必须在 commit 之后,否则 worker 读不到未提交的行,
job 静默卡在 queued(生产事故:同批 id=70 跑通、id=69 永久 queued)。

这些测试用真实 worker 线程 + 真实 commit 时序(不 mock enqueue),
覆盖原单测的盲区。"""
from __future__ import annotations

import time
from collections import Counter

import pytest

from app.db import SessionLocal, init_db
from app.models import OnDemandJob

pytestmark = pytest.mark.unit


def _fake_fetch(url, *, max_items=100, review_limit=100, do_persist=True):
    from app.ondemand.base import OnDemandResult
    r = OnDemandResult()
    r.add_listing({"sku": "X", "title": "t", "site": "ondemand_lazada"})
    return r


def _drain(ws, timeout=8.0):
    """轮询直到该 ws 无 queued/running,返回最终状态分布。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = SessionLocal()
        rows = s.query(OnDemandJob).filter(OnDemandJob.workspace_id == ws).all()
        statuses = Counter(r.status for r in rows)
        s.close()
        if not (statuses.get("queued") or statuses.get("running")):
            return dict(statuses)
        time.sleep(0.15)
    return dict(statuses)


def test_batch_jobs_all_get_processed_with_hot_worker(monkeypatch):
    """热 worker 下提交一批:所有 job 必须被处理,无一卡 queued。"""
    from app.ondemand import queue as q, runner
    from app.api import ondemand_jobs as oj
    from app.api.ondemand_jobs import submit_batch

    init_db()
    monkeypatch.setattr(runner, "fetch", _fake_fetch)
    WS = 91001
    s = SessionLocal()
    s.query(OnDemandJob).filter(OnDemandJob.workspace_id == WS).delete()
    s.commit(); s.close()

    # 预热 worker,使其阻塞在 _q.get()(复现"热 worker"真实生产态)
    q.ensure_worker()
    time.sleep(0.3)

    # 复刻路由时序:submit_batch 后由调用方 commit
    s = SessionLocal()
    out = submit_batch(s, ws_id=WS, username="t",
                       urls=[f"https://www.lazada.com.my/products/a-i{i}.html"
                             for i in range(5)],
                       max_items=20, review_limit=50)
    # 放大竞态窗口:commit 前停一下,模拟 worker 抢先取件
    time.sleep(0.2)
    s.commit(); s.close()
    oj.flush_enqueue(out)   # 路由在 commit 之后入队

    final = _drain(WS)
    assert final.get("queued", 0) == 0, f"有 job 卡在 queued: {final}"
    assert final.get("success") == 5, final


def test_retry_processed_after_commit(monkeypatch):
    from app.ondemand import queue as q, runner
    from app.api import ondemand_jobs as oj

    init_db()
    monkeypatch.setattr(runner, "fetch", _fake_fetch)
    WS = 91002
    s = SessionLocal()
    s.query(OnDemandJob).filter(OnDemandJob.workspace_id == WS).delete()
    j = OnDemandJob(url="https://www.lazada.com.my/products/a-i1.html",
                    platform="lazada", status="failed", batch_id="r",
                    max_items=20, review_limit=50, attempts=1,
                    workspace_id=WS, error="boom")
    s.add(j); s.commit(); jid = j.id; s.close()

    q.ensure_worker(); time.sleep(0.3)
    s = SessionLocal()
    out = oj.retry_job(s, ws_id=WS, job_id=jid)
    time.sleep(0.2)
    s.commit(); s.close()
    oj.flush_enqueue(out)

    final = _drain(WS)
    assert final.get("queued", 0) == 0, final
    assert final.get("success") == 1, final
