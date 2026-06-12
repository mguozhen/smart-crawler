# Spine 异步抓取队列 + 常驻 Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 SP1 数据脊柱加一层独立的异步抓取队列——任意 URL 入队 → 常驻 worker 消费 → 走 SP1 `resolve()` 落库,带重试退避,不碰现有电商队列与 SP1 同步入口。

**Architecture:** 新表 `spine_jobs` + `spine_queue.py`(enqueue/claim_job 乐观锁/execute_job/退避)+ `spine_worker.py`(常驻 loop + 信号优雅退出),全部复用 SP1 `spine.resolve()` 与现有鉴权。镜像生产验证过的 `runner.py`/`worker.py` 模式。

**Tech Stack:** FastAPI + SQLAlchemy(SQLite 本地 / PG 生产)、FastMCP、pytest。

**Spec:** `docs/superpowers/specs/2026-06-12-spine-async-queue-design.md`

**分支(待建):** `feat/spine-async-queue`

**关键复用点(已核实):**
- `spine.resolve(db, url, dataset, *, workspace_id, force_live=False, max_age_sec=None, save_policy="promote_if_valid", mode="standard") -> dict`(返回含 `record_id`);`spine.get_or_create_dataset(db, name, *, workspace_id, entity_type="generic", source_kind="custom_url") -> Dataset`。
- `runner.py::enqueue/claim_job` 乐观锁模式(`update(CrawlJob).where(id=, status="pending")`,`res.rowcount==1`)。
- `worker.py`:`signal.signal(SIGTERM/SIGINT, _stop)` + `_running` flag + `socket.gethostname()`+`os.getpid()`。
- `db.py`:`SessionLocal`、`session_scope()`、`init_db()` 里 `Base.metadata.create_all(engine)` 自动建新表(无需改 `_migrate`)。
- `models.py` 顶部已 import `Column, Integer, String, Text, Boolean, DateTime, ForeignKey`(均在用)、`from datetime import datetime`。
- v2 鉴权 helper:`_require_scope(db, authorization, x_api_key, scope)`、`_v2_ws_id(db, authorization, x_api_key)`、`_meter(...)`、`get_db`、`Header`、`Depends`、`BaseModel`(现有端点在用)。
- MCP:`@metered_tool(required_scope=, cacheable=)`、`SessionLocal`、`_ws_id_from_ctx(db)`(SP1 已加)。`@metered_tool` 后仍是普通函数,测试直接调用(test_spine_api.py 先例)。
- 测试 mock 边界:`patch("app.spine._do_scrape", side_effect=stub)`,stub 签名 `(db, url, **kw)`。

---

## 文件结构

| 文件 | 职责 | 新建/改 |
|---|---|---|
| `backend/app/models.py` | SpineJob 模型 | 改(追加) |
| `backend/app/spine_queue.py` | enqueue / claim_job / execute_job / 退避 | 新建 |
| `backend/app/spine_worker.py` | 常驻消费 loop + 信号处理 | 新建 |
| `backend/app/api/v2.py` | async 端点 + job 状态查询 | 改(追加) |
| `backend/app/mcp_server.py` | enqueue_custom_scrape + get_custom_job | 改(追加) |
| `deploy/spine-worker.service` | systemd unit | 新建 |
| `backend/tests/test_spine_queue.py` | 队列 + worker 单测 | 新建 |
| `backend/tests/test_spine_queue_api.py` | REST/MCP 端到端 | 新建 |

---

## Task 1: SpineJob 模型 + 建表演练

**Files:**
- Modify: `backend/app/models.py`(文件末尾追加)
- Test: `backend/tests/test_spine_queue.py`(新建)

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_spine_queue.py`:

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_spine_queue.py::test_spine_jobs_table_exists -v`
Expected: FAIL(表不存在)

- [ ] **Step 3: 加 SpineJob 模型**

在 `backend/app/models.py` 末尾追加(顶部 import 已齐全,无需改):

```python
class SpineJob(Base):
    """Spine 异步抓取队列 —— 任意 URL 入队,worker 消费走 spine.resolve 落库。

    状态机:pending(入队/待重试)→ running(worker 领取)→ success / failed
    与电商 crawl_jobs 完全独立。
    """

    __tablename__ = "spine_jobs"

    id = Column(Integer, primary_key=True)
    url = Column(Text)
    dataset = Column(String, index=True)
    entity_type = Column(String, default="generic")
    save_policy = Column(String, default="promote_if_valid")
    force_live = Column(Boolean, default=False)
    status = Column(String, default="pending", index=True)
    retries = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)
    next_attempt_at = Column(DateTime, index=True, default=datetime.utcnow)
    worker = Column(String)
    result_record_id = Column(Integer, nullable=True)
    error = Column(Text)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_spine_queue.py::test_spine_jobs_table_exists -v`
Expected: PASS

- [ ] **Step 5: 建表演练(真实库副本,零丢失)**

Run:
```bash
cd backend
cp ../data/smart_crawler.db /tmp/spinejob_rehearsal.db
DATABASE_URL="sqlite:////tmp/spinejob_rehearsal.db" .venv/bin/python -c "
from app.db import init_db; init_db(); init_db()
import sqlite3; c=sqlite3.connect('/tmp/spinejob_rehearsal.db')
assert c.execute('SELECT count(*) FROM spine_jobs').fetchone()[0] == 0
print('spine_jobs OK, products preserved:', c.execute('SELECT count(*) FROM products').fetchone()[0])
"
rm -f /tmp/spinejob_rehearsal.db
```
Expected: `spine_jobs OK, products preserved: <非0>` 无报错。

- [ ] **Step 6: Commit**

```bash
cd /Users/wangxiaokang/Documents/github/smart-crawler
git add backend/app/models.py backend/tests/test_spine_queue.py
git commit -m "feat(spine-queue): SpineJob model + spine_jobs table"
```

---

## Task 2: enqueue + claim_job(乐观锁)

**Files:**
- Create: `backend/app/spine_queue.py`
- Test: `backend/tests/test_spine_queue.py`(追加)

- [ ] **Step 1: 写失败测试**

追加到 `backend/tests/test_spine_queue.py`:

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_spine_queue.py -k "enqueue or claim" -v`
Expected: FAIL(No module named 'app.spine_queue')

- [ ] **Step 3: 建 spine_queue.py(enqueue + claim_job)**

新建 `backend/app/spine_queue.py`:

```python
"""Spine 异步抓取队列 —— 任意 URL 入队,worker 消费走 spine.resolve 落库。

队列即 spine_jobs 表(镜像 runner.py 的乐观锁模式):
  enqueue()     —— 入队一条 pending(REST / MCP / 内部调用)
  claim_job()   —— worker 原子领取最旧的、到期的 pending 任务
  execute_job() —— 执行已领取的任务:spine.resolve 落库 → 成功/重试/失败
与电商 crawl_jobs 完全独立。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import update
from sqlalchemy.orm import Session

from .db import session_scope
from .models import SpineJob


def enqueue(db: Session, url: str, dataset: str, *,
            entity_type: str = "generic",
            save_policy: str = "promote_if_valid",
            force_live: bool = False, max_retries: int = 3,
            workspace_id: int | None = None) -> int:
    """入队一条 spine 抓取任务,返回 job_id。调用方负责 commit。"""
    job = SpineJob(url=url, dataset=dataset, entity_type=entity_type,
                   save_policy=save_policy, force_live=force_live,
                   status="pending", retries=0, max_retries=max_retries,
                   next_attempt_at=datetime.utcnow(), workspace_id=workspace_id,
                   created_at=datetime.utcnow())
    db.add(job)
    db.flush()
    return job.id


def claim_job(worker_id: str) -> int | None:
    """worker 原子领取最旧的、next_attempt_at<=now 的 pending 任务。

    乐观锁:仅当仍为 pending 时领取,防多 worker 抢同一任务。返回 job_id 或 None。
    """
    with session_scope() as s:
        now = datetime.utcnow()
        job = (s.query(SpineJob)
               .filter(SpineJob.status == "pending",
                       SpineJob.next_attempt_at <= now)
               .order_by(SpineJob.id).first())
        if job is None:
            return None
        res = s.execute(
            update(SpineJob)
            .where(SpineJob.id == job.id, SpineJob.status == "pending")
            .values(status="running", worker=worker_id,
                    started_at=now))
        return job.id if res.rowcount == 1 else None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_spine_queue.py -k "enqueue or claim" -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
cd /Users/wangxiaokang/Documents/github/smart-crawler
git add backend/app/spine_queue.py backend/tests/test_spine_queue.py
git commit -m "feat(spine-queue): enqueue + claim_job (optimistic lock)"
```

---

## Task 3: execute_job + 失败重试退避

**Files:**
- Modify: `backend/app/spine_queue.py`
- Test: `backend/tests/test_spine_queue.py`(追加)

- [ ] **Step 1: 写失败测试**

追加到 `backend/tests/test_spine_queue.py`:

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_spine_queue.py -k execute -v`
Expected: FAIL(execute_job 不存在)

- [ ] **Step 3: 实现 execute_job + _handle_failure + _backoff**

在 `backend/app/spine_queue.py` 追加:

```python
def _backoff(retries: int) -> timedelta:
    """指数退避:1→30s, 2→2m, 3→10m, 之后封顶 10m。"""
    table = {1: 30, 2: 120, 3: 600}
    return timedelta(seconds=table.get(retries, 600))


def execute_job(job_id: int) -> dict:
    """执行一条已领取(running)的任务:spine.resolve 落库 → 成功/重试/失败。"""
    from . import spine
    with session_scope() as s:
        job = s.get(SpineJob, job_id)
        if job is None:
            raise ValueError(f"任务不存在: {job_id}")
        url = job.url
        dataset_name = job.dataset
        entity_type = job.entity_type or "generic"
        save_policy = job.save_policy or "promote_if_valid"
        force_live = bool(job.force_live)
        workspace_id = job.workspace_id
        try:
            ds = spine.get_or_create_dataset(
                s, dataset_name, workspace_id=workspace_id,
                entity_type=entity_type)
            out = spine.resolve(s, url, ds, workspace_id=workspace_id,
                                force_live=force_live, save_policy=save_policy)
            job.status = "success"
            job.result_record_id = out.get("record_id")
            job.finished_at = datetime.utcnow()
            job.error = None
            return {"job_id": job_id, "status": "success",
                    "record_id": out.get("record_id")}
        except Exception as exc:
            return _handle_failure(s, job, exc)


def _handle_failure(s: Session, job: SpineJob, exc: Exception) -> dict:
    """失败处理:未超限 → 回 pending + 退避;超限 → failed。"""
    job.retries = (job.retries or 0) + 1
    job.error = str(exc)
    if job.retries < (job.max_retries or 3):
        job.status = "pending"
        job.worker = None
        job.next_attempt_at = datetime.utcnow() + _backoff(job.retries)
        return {"job_id": job.id, "status": "pending", "retries": job.retries}
    job.status = "failed"
    job.finished_at = datetime.utcnow()
    return {"job_id": job.id, "status": "failed", "retries": job.retries}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_spine_queue.py -k execute -v`
Expected: 3 passed

- [ ] **Step 5: 退避门控测试(退避内 claim 不到)**

追加到 `backend/tests/test_spine_queue.py`:

```python
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
```

- [ ] **Step 6: 跑测试确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_spine_queue.py -k "execute or backoff" -v`
Expected: 4 passed

- [ ] **Step 7: Commit**

```bash
cd /Users/wangxiaokang/Documents/github/smart-crawler
git add backend/app/spine_queue.py backend/tests/test_spine_queue.py
git commit -m "feat(spine-queue): execute_job + retry/backoff"
```

---

## Task 4: 常驻 worker(run_loop + 信号优雅退出)

**Files:**
- Create: `backend/app/spine_worker.py`
- Test: `backend/tests/test_spine_queue.py`(追加)

- [ ] **Step 1: 写失败测试**

追加到 `backend/tests/test_spine_queue.py`:

```python
def test_run_loop_consumes_one_job_then_stops():
    init_db()
    _clear_pending()  # 清场,保证 run_loop 消费的是本测试入队的 job
    s = SessionLocal()
    from app.spine_queue import enqueue
    jid = enqueue(s, "https://x.com/p/loop", "loop-set", entity_type="product",
                  save_policy="main", workspace_id=None)
    s.commit(); s.close()
    import app.spine_worker as sw
    # should_continue:第一轮 True,之后 False —— 只消费一轮
    calls = {"n": 0}
    def once():
        calls["n"] += 1
        return calls["n"] <= 1
    with patch("app.spine._do_scrape", side_effect=_scrape_stub):
        sw.run_loop(poll_interval=0, should_continue=once)
    s2 = SessionLocal()
    job = s2.get(SpineJob, jid)
    assert job.status == "success"
    s2.close()


def test_run_loop_empty_queue_no_crash():
    init_db()
    _clear_pending()  # 空队列
    import app.spine_worker as sw
    calls = {"n": 0}
    def once():
        calls["n"] += 1
        return calls["n"] <= 1
    # 空队列:领不到 job,sleep(poll_interval=0)一拍,should_continue 转 False 退出
    sw.run_loop(poll_interval=0, should_continue=once)  # 不抛异常即通过
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_spine_queue.py -k run_loop -v`
Expected: FAIL(No module named 'app.spine_worker')

- [ ] **Step 3: 建 spine_worker.py**

新建 `backend/app/spine_worker.py`:

```python
"""Spine 队列 worker —— 轮询 spine_jobs,执行抓取任务。

用法:
  · 独立进程: python -m app.spine_worker(服务化部署,可起多副本)
镜像 app/worker.py 模式,但消费 spine 队列、走 spine.resolve。
"""
from __future__ import annotations

import logging
import os
import signal
import socket
import time

from .spine_queue import claim_job, execute_job

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [spine-worker] %(message)s")
logger = logging.getLogger("smart-crawler.spine-worker")

WORKER_ID = os.environ.get("SPINE_WORKER_ID") or \
    f"spine-{socket.gethostname()}-{os.getpid()}"
POLL_INTERVAL = int(os.environ.get("SPINE_WORKER_POLL", "5"))
_running = True


def run_loop(poll_interval: int | None = None, should_continue=None) -> None:
    """领取并执行队列任务,直到 should_continue() 为假。"""
    interval = POLL_INTERVAL if poll_interval is None else poll_interval
    should_continue = should_continue or (lambda: _running)
    logger.info("spine-worker %s 启动,轮询间隔 %ds", WORKER_ID, interval)
    while should_continue():
        try:
            job_id = claim_job(WORKER_ID)
        except Exception as exc:
            logger.error("领取任务失败: %s", exc)
            time.sleep(interval)
            continue
        if job_id is None:
            time.sleep(interval)
            continue
        try:
            result = execute_job(job_id)
            logger.info("job %s -> %s", job_id, result.get("status"))
        except Exception as exc:
            # execute_job 内部已兜失败;这里只防御未预期异常,worker 永不挂
            logger.error("执行 job %s 异常: %s", job_id, exc)


def _stop(*_):
    global _running
    _running = False


def main() -> None:
    from .db import init_db
    init_db()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    run_loop()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_spine_queue.py -k run_loop -v`
Expected: 2 passed

- [ ] **Step 5: 全量回归(spine 队列单测)**

Run: `cd backend && .venv/bin/python -m pytest tests/test_spine_queue.py -q`
Expected: 全 passed(Task1-4 累计)

- [ ] **Step 6: Commit**

```bash
cd /Users/wangxiaokang/Documents/github/smart-crawler
git add backend/app/spine_worker.py backend/tests/test_spine_queue.py
git commit -m "feat(spine-queue): resident spine_worker run_loop + signals"
```

---

## Task 5: systemd unit

**Files:**
- Create: `deploy/spine-worker.service`

- [ ] **Step 1: 写 unit 文件**

新建 `deploy/spine-worker.service`:

```ini
[Unit]
Description=smart-crawler spine async queue worker
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/smart-crawler/backend
ExecStart=/opt/smart-crawler/.venv/bin/python -m app.spine_worker
Environment=SPINE_WORKER_POLL=5
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: 校验文件存在 + 关键字段**

Run: `cd /Users/wangxiaokang/Documents/github/smart-crawler && grep -E "ExecStart=.*spine_worker|Restart=always" deploy/spine-worker.service`
Expected: 两行均命中。

- [ ] **Step 3: Commit**

```bash
cd /Users/wangxiaokang/Documents/github/smart-crawler
git add deploy/spine-worker.service
git commit -m "feat(spine-queue): systemd unit for spine worker"
```

---

## Task 6: REST 异步端点 + job 状态查询

**Files:**
- Modify: `backend/app/api/v2.py`
- Test: `backend/tests/test_spine_queue_api.py`(新建)

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_spine_queue_api.py`:

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_spine_queue_api.py -k v2_async -v`
Expected: FAIL(404,路由未加)

- [ ] **Step 3: 加两个端点**

在 `backend/app/api/v2.py` 末尾追加(顶部 import 区补 `from .. import spine_queue`;`spine`、`get_db`、`Header`、`Depends`、`BaseModel`、`_require_scope`、`_v2_ws_id` 现有在用):

```python
class AsyncScrapeRequest(BaseModel):
    url: str
    dataset: str
    entity_type: str = "generic"
    save_policy: str = "promote_if_valid"
    force_live: bool = False
    max_retries: int = 3


@router.post("/custom/scrape/async")
def custom_scrape_async(req: AsyncScrapeRequest,
                        authorization: str = Header(default=""),
                        x_api_key: str = Header(default="", alias="X-API-Key"),
                        db: Session = Depends(get_db)):
    """异步入队一条通用抓取任务,返回 job_id。worker 消费走 warehouse-first 落库。"""
    _require_scope(db, authorization, x_api_key, "crawler:scrape")
    ws = _v2_ws_id(db, authorization, x_api_key)
    job_id = spine_queue.enqueue(db, req.url, req.dataset,
                                 entity_type=req.entity_type,
                                 save_policy=req.save_policy,
                                 force_live=req.force_live,
                                 max_retries=req.max_retries, workspace_id=ws)
    db.commit()
    return {"job_id": job_id, "status": "pending"}


@router.get("/custom/job/{job_id}")
def custom_job_status(job_id: int,
                      authorization: str = Header(default=""),
                      x_api_key: str = Header(default="", alias="X-API-Key"),
                      db: Session = Depends(get_db)):
    """查询 spine 抓取任务状态。"""
    _require_scope(db, authorization, x_api_key, "crawler:read")
    from ..models import SpineJob
    job = db.get(SpineJob, job_id)
    if job is None:
        raise HTTPException(404, {"error": "job_not_found", "job_id": job_id})
    return {
        "job_id": job.id, "status": job.status, "url": job.url,
        "dataset": job.dataset, "retries": job.retries,
        "max_retries": job.max_retries,
        "result_record_id": job.result_record_id, "error": job.error,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }
```

确认 v2.py 顶部已有 `HTTPException`(现有端点在用,是)。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_spine_queue_api.py -k v2_async -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
cd /Users/wangxiaokang/Documents/github/smart-crawler
git add backend/app/api/v2.py backend/tests/test_spine_queue_api.py
git commit -m "feat(spine-queue): v2 async enqueue + job status endpoints"
```

---

## Task 7: MCP 工具 enqueue_custom_scrape + get_custom_job

**Files:**
- Modify: `backend/app/mcp_server.py`
- Test: `backend/tests/test_spine_queue_api.py`(追加)

- [ ] **Step 1: 写失败测试**

追加到 `backend/tests/test_spine_queue_api.py`:

```python
def test_mcp_enqueue_and_get_job():
    init_db()
    from app import mcp_server
    out = mcp_server.enqueue_custom_scrape(
        url="https://x.com/p/mcp", dataset="mcpq-set", entity_type="product",
        save_policy="main")
    jid = out["job_id"]
    assert out["status"] == "pending"
    # 消费
    from app.spine_queue import claim_job, execute_job
    assert claim_job("test-worker") == jid
    with patch("app.spine._do_scrape", side_effect=_scrape_stub):
        execute_job(jid)
    got = mcp_server.get_custom_job(job_id=jid)
    assert got["status"] == "success" and got["result_record_id"] is not None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_spine_queue_api.py -k mcp_enqueue -v`
Expected: FAIL(工具不存在)

- [ ] **Step 3: 实现两个 MCP 工具**

在 `backend/app/mcp_server.py` 末尾(其他 `@metered_tool` 之后、`if __name__` 之前)追加:

```python
@metered_tool(required_scope="crawler:scrape", cacheable=False)
def enqueue_custom_scrape(url: str, dataset: str, entity_type: str = "generic",
                          save_policy: str = "promote_if_valid",
                          force_live: bool = False, max_retries: int = 3) -> dict:
    """异步入队一条通用抓取任务,返回 job_id。

    任意 URL → 入 spine 队列,常驻 worker 消费走 warehouse-first 落库。
    适合批量/不需立即拿结果的场景。用 get_custom_job(job_id) 查进度。
    save_policy: promote_if_valid(默认)/staging/main。force_live=true 强制实时抓。
    """
    from . import spine_queue
    s = SessionLocal()
    try:
        ws = _ws_id_from_ctx(s)
        job_id = spine_queue.enqueue(s, url, dataset, entity_type=entity_type,
                                     save_policy=save_policy, force_live=force_live,
                                     max_retries=max_retries, workspace_id=ws)
        s.commit()
        return {"job_id": job_id, "status": "pending"}
    finally:
        s.close()


@metered_tool(required_scope="crawler:read", cacheable=False)
def get_custom_job(job_id: int) -> dict:
    """查询 spine 异步抓取任务状态:status/retries/result_record_id/error。"""
    from .models import SpineJob
    s = SessionLocal()
    try:
        job = s.get(SpineJob, job_id)
        if job is None:
            return {"error": "job_not_found", "job_id": job_id}
        return {
            "job_id": job.id, "status": job.status, "url": job.url,
            "dataset": job.dataset, "retries": job.retries,
            "max_retries": job.max_retries,
            "result_record_id": job.result_record_id, "error": job.error,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        }
    finally:
        s.close()
```

确认 mcp_server.py 顶部已 import `SessionLocal`(现有工具在用,是)。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_spine_queue_api.py -k mcp_enqueue -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/wangxiaokang/Documents/github/smart-crawler
git add backend/app/mcp_server.py backend/tests/test_spine_queue_api.py
git commit -m "feat(spine-queue): enqueue_custom_scrape + get_custom_job MCP tools"
```

---

## Task 8: 端到端验证 + memory

**Files:** 无(验证)

- [ ] **Step 1: 后端全量回归**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: 全 passed(原有 204 + spine 队列新增),无回归。

- [ ] **Step 2: 建表演练复核(真实库副本)**

Run:
```bash
cd backend
cp ../data/smart_crawler.db /tmp/spineq_e2e.db
DATABASE_URL="sqlite:////tmp/spineq_e2e.db" .venv/bin/python -c "
from app.db import init_db; init_db(); init_db()
import sqlite3; c=sqlite3.connect('/tmp/spineq_e2e.db')
print('products preserved:', c.execute('SELECT count(*) FROM products').fetchone()[0])
print('spine_jobs rows:', c.execute('SELECT count(*) FROM spine_jobs').fetchone()[0])
"
rm -f /tmp/spineq_e2e.db
```
Expected: products 非 0,spine_jobs 0,无报错。

- [ ] **Step 3: 端到端脚本(mock 抓取,验队列闭环)**

Run:
```bash
cd backend && .venv/bin/python -c "
from unittest.mock import patch
from app.db import init_db, SessionLocal
from app import spine_queue
import app.spine_worker as sw
init_db(); s = SessionLocal()
jid = spine_queue.enqueue(s, 'https://x.com/p/e2e', 'e2e-q', entity_type='product', save_policy='main', workspace_id=None)
s.commit(); s.close()
def stub(db,url,**kw): return {'scrape_id':'x','url':url,'data':{'title':'E2E','confidence':0.95},'metadata':{'canonical':None},'html':'<html>x</html>','warnings':[],'usage':{'source':'live','credits_used':2}}
calls={'n':0}
def once():
    calls['n']+=1; return calls['n']<=1
with patch('app.spine._do_scrape', side_effect=stub):
    sw.run_loop(poll_interval=0, should_continue=once)
s2=SessionLocal()
from app.models import SpineJob
job=s2.get(SpineJob, jid)
print('job status:', job.status, 'record_id:', job.result_record_id)
assert job.status=='success' and job.result_record_id is not None
from app import spine
ds=spine.get_or_create_dataset(s2,'e2e-q',workspace_id=None)
print('query total:', spine.query_dataset(s2, ds, query='E2E')['total'])
s2.close()
"
```
Expected: `job status: success record_id: <非None>` + `query total: 1`。

- [ ] **Step 4: 更新 memory**

新建 memory 文件记录:spine 异步队列已建(spine_jobs 表 + spine_queue + spine_worker + 异步 REST/MCP 入口 + systemd unit),独立于电商 crawl_jobs,worker 部署要 `cp deploy/spine-worker.service`,未部署。在 MEMORY.md 加索引行。

- [ ] **Step 5: 不自动部署**。汇报完成,等用户决定 commit 之外的部署动作。

---

## Self-Review(写计划者已核对)

- **Spec 覆盖**:§1 SpineJob 模型→Task1;§2 enqueue/claim_job→Task2,execute_job/退避→Task3;§3 spine_worker→Task4;§4 systemd→Task5;§5 REST 异步端点+状态查询→Task6,MCP 工具→Task7;§6 测试策略贯穿 Task1-7,端到端→Task8。全覆盖。
- **类型/签名一致**:`enqueue(db, url, dataset, *, entity_type, save_policy, force_live, max_retries, workspace_id)->int`、`claim_job(worker_id)->int|None`、`execute_job(job_id)->dict`、`_handle_failure(s, job, exc)->dict`、`_backoff(retries)->timedelta`、`run_loop(poll_interval=None, should_continue=None)` 跨 Task 一致;REST/MCP 都调同一组 spine_queue 函数。
- **复用点已核实**:`spine.resolve` 返回含 `record_id`(spine.py:154/194);`runner.py` 乐观锁 `res.rowcount==1`;worker 信号 `_stop`+`_running`;`_v2_ws_id`/`_ws_id_from_ctx` SP1 已加;`@metered_tool` 后可直接调用。
- **无占位符**:每步含完整代码与命令。
- **已知风险(实现时核实)**:
  1. `claim_job`/`execute_job` 用 `session_scope()` 独立事务,与测试里 `SessionLocal()` 不同 session——测试中先 `s.commit(); s.close()` 再 claim,避免读不到;execute 后用新 session 复查(计划测试已这么写)。
  2. `spine.resolve` 内部对 `force_live=True` 会强制走 `_do_scrape`;测试 mock `app.spine._do_scrape` 覆盖网络层,与 SP1 测试同款边界。
  3. v2 端点 `db.commit()` 后返回 job_id——`enqueue` 只 flush 不 commit,提交责任在端点(与 MCP 工具 `s.commit()` 一致)。
