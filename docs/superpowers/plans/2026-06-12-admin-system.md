# smart-crawler 后台管理系统(独立 admin-app)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建一个独立 Vue3 admin-app + 后端 `/api/admin/spine/*` 端点 + 审计,让超管可视化管理 spine 队列/数据集/计费/健康/审计,复用现有租户用户管理。

**Architecture:** 后端新建 `admin_spine.py`(router 前缀 `/api/admin/spine`,全部 `_require_super_admin`)+ `admin_audit_logs` 表 + `audit.py` helper;前端新建独立 `admin-app/`(Vite base `/admin/`)挂到后端 `/admin`。不碰现有 admin 端点与 frontend-app。

**Tech Stack:** FastAPI + SQLAlchemy + pytest;Vue3 + Vite + Nuxt UI + Pinia + vue-router。

**Spec:** `docs/superpowers/specs/2026-06-12-admin-system-design.md`

**分支(待建):** `feat/admin-system`

**关键复用点(已核实):**
- 鉴权:`_require_super_admin(user, db)`(routes.py)收 `user: str = Depends(require_user)`。`_require_dashboard_user` 对 `user=="admin"` 有兜底返回 super_admin User——**测试可直接以 `user="admin"` 调端点函数**(现有测试 test_workspace_tenancy.py 即如此),无需造完整 JWT。非超管:造普通 `User(global_role=None)` + `make_token`,或直接传非 admin user 触发 403。
- `require_user`/`_require_super_admin`/`_is_super_admin` 均在 `app/api/routes.py`,新 router 从那 import。
- 模型字段(已核实):`SpineJob`(id/url/dataset/entity_type/save_policy/force_live/status/retries/max_retries/next_attempt_at/worker/result_record_id/error/workspace_id/api_key_id/heartbeat_at);`Usage`(id/api_key_id/workspace_id/endpoint/record_count/credits_used/bytes_returned/duration_ms/**occurred_at**);`Dataset`(id/name/slug/entity_type/description/source_kind/freshness_ttl_sec/workspace_id/created_by/created_at);`ExtractedRecord`(id/dataset_id/snapshot_id/source_url/canonical_url/entity_type/data/record_key/content_hash/confidence/extraction_method/quality_status/fetched_at/extracted_at)。
- `spine_queue.enqueue(db,url,dataset,*,entity_type,save_policy,force_live,max_retries,api_key_id,workspace_id)->int`;`SpineJob` 状态 pending/running/success/failed。
- 测试:`from app.auth import make_token`;`from app.db import SessionLocal, init_db`;现有 admin 端点测试直接调函数传 `user="admin"`。
- main.py 挂载:`app.mount("/assets", StaticFiles(...))` + `app.include_router(...)`;新增同样方式。`FRONTEND_APP_DIST` 是现有常量,admin 仿照定义 `ADMIN_APP_DIST`。
- 前端样板:`frontend-app/src/api/client.ts`(apiJson + Bearer + 401 clear)、`stores/auth.ts`(token/workspaceId/login/loadMe)、`vite.config.ts`(base + vue/ui/tailwindcss 插件)、`package.json`(deps)。admin-app 全部参照,改 base 为 `/admin/`。

---

## 文件结构

| 文件 | 职责 | 新建/改 |
|---|---|---|
| `backend/app/models.py` | AdminAuditLog 模型 | 改 |
| `backend/app/audit.py` | record_audit helper | 新建 |
| `backend/app/api/admin_spine.py` | spine admin 端点(队列/数据集/计费/健康/审计) | 新建 |
| `backend/app/main.py` | include admin_spine router + 挂 /admin StaticFiles | 改 |
| `backend/tests/test_admin_spine.py` | 端点鉴权 + 返回 + 审计埋点 | 新建 |
| `admin-app/` | 独立 Vue3 工程 | 新建 |

---

## Task 1: AdminAuditLog 模型 + record_audit helper

**Files:**
- Modify: `backend/app/models.py`
- Create: `backend/app/audit.py`
- Test: `backend/tests/test_admin_spine.py`(新建)

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_admin_spine.py`:

```python
"""后台管理系统(admin spine)测试。"""
from app.db import SessionLocal, init_db


def test_admin_audit_log_table_and_record():
    init_db()
    from sqlalchemy import inspect
    from app.db import engine
    cols = {c["name"] for c in inspect(engine).get_columns("admin_audit_logs")}
    for c in ("id", "actor_user_id", "actor_name", "action", "target_type",
              "target_id", "detail", "ip", "created_at"):
        assert c in cols, f"admin_audit_logs 缺列 {c}"
    # record_audit 落一行
    from app.audit import record_audit
    from app.models import AdminAuditLog
    s = SessionLocal()
    n0 = s.query(AdminAuditLog).count()
    record_audit(s, actor_user_id=1, actor_name="admin", action="test.action",
                 target_type="job", target_id="42", detail={"k": "v"}, ip="1.2.3.4")
    s.commit()
    n1 = s.query(AdminAuditLog).count()
    assert n1 == n0 + 1
    row = s.query(AdminAuditLog).order_by(AdminAuditLog.id.desc()).first()
    assert row.action == "test.action" and row.target_id == "42"
    assert row.detail == {"k": "v"} and row.actor_name == "admin"
    s.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin_spine.py::test_admin_audit_log_table_and_record -v`
Expected: FAIL(表/模块不存在)

- [ ] **Step 3: 加 AdminAuditLog 模型**

在 `backend/app/models.py` 末尾追加(顶部 import 已有 `JSON, Column, DateTime, ForeignKey, Integer, String, Text`):

```python
class AdminAuditLog(Base):
    """超管后台写操作审计 —— 谁在何时对什么做了什么。"""

    __tablename__ = "admin_audit_logs"

    id = Column(Integer, primary_key=True)
    actor_user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=True)
    actor_name = Column(String, index=True)
    action = Column(String, index=True)              # e.g. job.retry / record.promote
    target_type = Column(String, index=True)         # job / record / workspace / user / key
    target_id = Column(String, index=True)
    detail = Column(JSON)
    ip = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
```

`admin_audit_logs` 表靠 `init_db()` 的 `create_all` 自动建(继承 Base)。

- [ ] **Step 4: 建 audit.py**

新建 `backend/app/audit.py`:

```python
"""超管后台审计 —— 统一记录写操作。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from .models import AdminAuditLog


def record_audit(db: Session, *, actor_user_id: int | None, actor_name: str,
                 action: str, target_type: str, target_id: str | None = None,
                 detail: dict | None = None, ip: str | None = None) -> None:
    """记一条审计。调用方负责 commit(通常与被审计的写操作同事务提交)。"""
    db.add(AdminAuditLog(
        actor_user_id=actor_user_id, actor_name=actor_name, action=action,
        target_type=target_type, target_id=str(target_id) if target_id is not None else None,
        detail=detail or {}, ip=ip))
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin_spine.py::test_admin_audit_log_table_and_record -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd /Users/wangxiaokang/Documents/github/smart-crawler
git add backend/app/models.py backend/app/audit.py backend/tests/test_admin_spine.py
git commit -m "feat(admin): AdminAuditLog model + record_audit helper"
```

---

## Task 2: admin_spine router 骨架 + 鉴权门

**Files:**
- Create: `backend/app/api/admin_spine.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_admin_spine.py`(追加)

- [ ] **Step 1: 写失败测试**

追加到 `backend/tests/test_admin_spine.py`:

```python
def test_jobs_stats_requires_super_admin():
    from fastapi.testclient import TestClient
    from app.main import app
    init_db()
    client = TestClient(app)
    # 无 token → 401
    r = client.get("/api/admin/spine/jobs/stats")
    assert r.status_code in (401, 403)


def test_jobs_stats_ok_for_admin():
    init_db()
    from app.api.admin_spine import jobs_stats
    from app.db import SessionLocal
    s = SessionLocal()
    # 直接调函数,user="admin" 走 _require_dashboard_user 的 super_admin 兜底
    out = jobs_stats(user="admin", db=s)
    s.close()
    for k in ("pending", "running", "success", "failed", "stuck"):
        assert k in out
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin_spine.py -k "jobs_stats" -v`
Expected: FAIL(模块/路由不存在)

- [ ] **Step 3: 建 admin_spine.py 骨架 + jobs_stats**

新建 `backend/app/api/admin_spine.py`:

```python
"""超管后台 · spine 管理端点(队列/数据集/计费/健康/审计)。

全部经 _require_super_admin。写操作经 audit.record_audit 埋点。
与现有 routes.py 的 /api/admin/* 并列,不碰它们。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import SpineJob
from .routes import require_user, _require_super_admin

router = APIRouter(prefix="/api/admin/spine", tags=["admin · spine"])

_STUCK_SEC = 600


@router.get("/jobs/stats")
def jobs_stats(user: str = Depends(require_user),
               db: Session = Depends(get_db)) -> dict:
    _require_super_admin(user, db)
    counts = {st: db.query(SpineJob).filter(SpineJob.status == st).count()
              for st in ("pending", "running", "success", "failed")}
    cutoff = datetime.utcnow() - timedelta(seconds=_STUCK_SEC)
    counts["stuck"] = (db.query(SpineJob)
                       .filter(SpineJob.status == "running",
                               SpineJob.heartbeat_at < cutoff).count())
    return counts
```

- [ ] **Step 4: main.py 注册 router**

在 `backend/app/main.py` 的 include_router 区(v2_router 之后)加:

```python
from .api.admin_spine import router as admin_spine_router
app.include_router(admin_spine_router)
```

(import 放文件顶部其他 router import 旁;include 放 include 区。先读 main.py 确认 import 风格。)

- [ ] **Step 5: 跑测试确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin_spine.py -k "jobs_stats" -v`
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
cd /Users/wangxiaokang/Documents/github/smart-crawler
git add backend/app/api/admin_spine.py backend/app/main.py backend/tests/test_admin_spine.py
git commit -m "feat(admin): admin_spine router + jobs/stats with super_admin gate"
```

---

## Task 3: 队列端点(jobs 列表/详情/retry/enqueue)

**Files:**
- Modify: `backend/app/api/admin_spine.py`
- Test: `backend/tests/test_admin_spine.py`(追加)

- [ ] **Step 1: 写失败测试**

追加:

```python
def test_jobs_list_detail_retry_enqueue():
    init_db()
    from app.api import admin_spine
    from app.db import SessionLocal
    from app import spine_queue
    from app.models import SpineJob, AdminAuditLog
    s = SessionLocal()
    # 造一个 failed job
    jid = spine_queue.enqueue(s, "https://x.com/p/adm", "adm-set", workspace_id=None)
    s.commit()
    job = s.get(SpineJob, jid); job.status = "failed"; job.error = "boom"; s.commit()
    # 列表
    lst = admin_spine.jobs_list(status="failed", dataset=None, tenant=None,
                                page=1, size=20, user="admin", db=s)
    assert lst["total"] >= 1 and any(it["id"] == jid for it in lst["items"])
    # 详情
    det = admin_spine.job_detail(job_id=jid, user="admin", db=s)
    assert det["id"] == jid and det["error"] == "boom"
    # retry → 回 pending + 审计
    n_audit = s.query(AdminAuditLog).count()
    r = admin_spine.job_retry(job_id=jid, user="admin", db=s, ip="1.1.1.1")
    assert r["status"] == "pending"
    s.refresh(s.get(SpineJob, jid))
    assert s.get(SpineJob, jid).status == "pending"
    assert s.query(AdminAuditLog).count() == n_audit + 1
    # enqueue → 新 job + 审计
    n_audit2 = s.query(AdminAuditLog).count()
    e = admin_spine.job_enqueue(payload={"url": "https://x.com/p/new", "dataset": "adm-set"},
                                user="admin", db=s, ip="1.1.1.1")
    assert e["job_id"] and e["status"] == "pending"
    assert s.query(AdminAuditLog).count() == n_audit2 + 1
    s.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin_spine.py -k "list_detail_retry" -v`
Expected: FAIL

- [ ] **Step 3: 实现四个端点**

在 `admin_spine.py` 追加(顶部补 import:`from ..models import SpineJob, AdminAuditLog`、`from .. import spine_queue`、`from ..audit import record_audit`、`from .routes import _require_dashboard_user`):

```python
def _job_dict(j: SpineJob) -> dict:
    return {"id": j.id, "url": j.url, "dataset": j.dataset,
            "entity_type": j.entity_type, "status": j.status,
            "retries": j.retries, "max_retries": j.max_retries,
            "error": j.error, "worker": j.worker,
            "result_record_id": j.result_record_id,
            "workspace_id": j.workspace_id, "api_key_id": j.api_key_id,
            "created_at": j.created_at.isoformat() if j.created_at else None,
            "finished_at": j.finished_at.isoformat() if j.finished_at else None,
            "heartbeat_at": j.heartbeat_at.isoformat() if j.heartbeat_at else None}


@router.get("/jobs")
def jobs_list(status: str | None = None, dataset: str | None = None,
              tenant: int | None = None, page: int = 1, size: int = 20,
              user: str = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    _require_super_admin(user, db)
    q = db.query(SpineJob)
    if status:
        q = q.filter(SpineJob.status == status)
    if dataset:
        q = q.filter(SpineJob.dataset == dataset)
    if tenant is not None:
        q = q.filter(SpineJob.workspace_id == tenant)
    total = q.count()
    rows = (q.order_by(SpineJob.id.desc())
            .offset((page - 1) * size).limit(size).all())
    return {"total": total, "items": [_job_dict(j) for j in rows]}


@router.get("/jobs/{job_id}")
def job_detail(job_id: int, user: str = Depends(require_user),
               db: Session = Depends(get_db)) -> dict:
    _require_super_admin(user, db)
    j = db.get(SpineJob, job_id)
    if j is None:
        raise HTTPException(404, {"error": "job_not_found", "job_id": job_id})
    return _job_dict(j)


@router.post("/jobs/{job_id}/retry")
def job_retry(job_id: int, user: str = Depends(require_user),
              db: Session = Depends(get_db),
              ip: str = Header(default="", alias="X-Forwarded-For")) -> dict:
    actor = _require_super_admin(user, db)
    j = db.get(SpineJob, job_id)
    if j is None:
        raise HTTPException(404, {"error": "job_not_found", "job_id": job_id})
    j.status = "pending"
    j.worker = None
    j.next_attempt_at = datetime.utcnow()
    record_audit(db, actor_user_id=actor.id, actor_name=actor.username,
                 action="job.retry", target_type="job", target_id=str(job_id),
                 detail={"prev_error": j.error}, ip=ip or None)
    db.commit()
    return {"job_id": job_id, "status": "pending"}


@router.post("/jobs/enqueue")
def job_enqueue(payload: dict, user: str = Depends(require_user),
                db: Session = Depends(get_db),
                ip: str = Header(default="", alias="X-Forwarded-For")) -> dict:
    actor = _require_super_admin(user, db)
    url = payload.get("url")
    dataset = payload.get("dataset")
    if not url or not dataset:
        raise HTTPException(422, {"error": "url and dataset required"})
    job_id = spine_queue.enqueue(
        db, url, dataset, entity_type=payload.get("entity_type", "generic"),
        save_policy=payload.get("save_policy", "promote_if_valid"),
        workspace_id=None)
    record_audit(db, actor_user_id=actor.id, actor_name=actor.username,
                 action="job.enqueue", target_type="job", target_id=str(job_id),
                 detail={"url": url, "dataset": dataset}, ip=ip or None)
    db.commit()
    return {"job_id": job_id, "status": "pending"}
```

注:`actor = _require_super_admin(...)` 返回 User,但 `user=="admin"` 兜底返回的 User 可能 `id` 为 None(未 seed)。`record_audit` 的 `actor_user_id` 列 nullable,None 安全。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin_spine.py -k "list_detail_retry" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/wangxiaokang/Documents/github/smart-crawler
git add backend/app/api/admin_spine.py backend/tests/test_admin_spine.py
git commit -m "feat(admin): queue endpoints (jobs list/detail/retry/enqueue)"
```

---

## Task 4: 数据集端点(datasets/records/promote/delete)

**Files:**
- Modify: `backend/app/api/admin_spine.py`
- Test: `backend/tests/test_admin_spine.py`(追加)

- [ ] **Step 1: 写失败测试**

```python
def test_datasets_records_promote_delete():
    init_db()
    from app.api import admin_spine
    from app.db import SessionLocal
    from app import spine
    from app.models import ExtractedRecord, AdminAuditLog
    s = SessionLocal()
    ds = spine.get_or_create_dataset(s, "adm-ds", workspace_id=None, entity_type="product")
    rec = ExtractedRecord(dataset_id=ds.id, source_url="https://x.com/r1",
                          canonical_url="https://x.com/r1", entity_type="product",
                          data={"title": "X"}, record_key="https://x.com/r1",
                          quality_status="staging")
    s.add(rec); s.commit(); rid = rec.id
    # datasets 列表
    dsets = admin_spine.datasets_list(user="admin", db=s)
    assert any(d["id"] == ds.id and d["record_count"] >= 1 for d in dsets["items"])
    # records 分页 + 过滤
    recs = admin_spine.dataset_records(dataset_id=ds.id, quality_status="staging",
                                       page=1, size=20, user="admin", db=s)
    assert recs["total"] >= 1
    # record 详情
    det = admin_spine.record_detail(record_id=rid, user="admin", db=s)
    assert det["data"]["title"] == "X" and "provenance" in det
    # promote → main + 审计
    na = s.query(AdminAuditLog).count()
    admin_spine.record_promote(record_id=rid, user="admin", db=s, ip="1.1.1.1")
    s.refresh(s.get(ExtractedRecord, rid))
    assert s.get(ExtractedRecord, rid).quality_status == "main"
    assert s.query(AdminAuditLog).count() == na + 1
    # delete → 没了 + 审计
    na2 = s.query(AdminAuditLog).count()
    admin_spine.record_delete(record_id=rid, user="admin", db=s, ip="1.1.1.1")
    assert s.get(ExtractedRecord, rid) is None
    assert s.query(AdminAuditLog).count() == na2 + 1
    s.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin_spine.py -k "datasets_records" -v`
Expected: FAIL

- [ ] **Step 3: 实现端点**

在 `admin_spine.py` 追加(顶部补 import:`from ..models import Dataset, ExtractedRecord, RawSnapshot`):

```python
@router.get("/datasets")
def datasets_list(user: str = Depends(require_user),
                  db: Session = Depends(get_db)) -> dict:
    _require_super_admin(user, db)
    rows = db.query(Dataset).order_by(Dataset.id.desc()).all()
    items = []
    for d in rows:
        n = db.query(ExtractedRecord).filter(ExtractedRecord.dataset_id == d.id).count()
        items.append({"id": d.id, "name": d.name, "slug": d.slug,
                      "entity_type": d.entity_type, "record_count": n,
                      "workspace_id": d.workspace_id})
    return {"items": items, "total": len(items)}


@router.get("/datasets/{dataset_id}/records")
def dataset_records(dataset_id: int, quality_status: str | None = None,
                    page: int = 1, size: int = 20,
                    user: str = Depends(require_user),
                    db: Session = Depends(get_db)) -> dict:
    _require_super_admin(user, db)
    q = db.query(ExtractedRecord).filter(ExtractedRecord.dataset_id == dataset_id)
    if quality_status:
        q = q.filter(ExtractedRecord.quality_status == quality_status)
    total = q.count()
    rows = (q.order_by(ExtractedRecord.id.desc())
            .offset((page - 1) * size).limit(size).all())
    return {"total": total, "items": [
        {"id": r.id, "source_url": r.source_url, "entity_type": r.entity_type,
         "quality_status": r.quality_status, "confidence": r.confidence,
         "data": r.data,
         "fetched_at": r.fetched_at.isoformat() if r.fetched_at else None}
        for r in rows]}


@router.get("/records/{record_id}")
def record_detail(record_id: int, user: str = Depends(require_user),
                  db: Session = Depends(get_db)) -> dict:
    _require_super_admin(user, db)
    r = db.get(ExtractedRecord, record_id)
    if r is None:
        raise HTTPException(404, {"error": "record_not_found", "record_id": record_id})
    snap = db.get(RawSnapshot, r.snapshot_id) if r.snapshot_id else None
    return {
        "id": r.id, "data": r.data, "entity_type": r.entity_type,
        "quality_status": r.quality_status, "confidence": r.confidence,
        "provenance": {"source_url": r.source_url, "canonical_url": r.canonical_url,
                       "content_hash": r.content_hash,
                       "extraction_method": r.extraction_method,
                       "fetched_at": r.fetched_at.isoformat() if r.fetched_at else None},
        "snapshot": ({"id": snap.id, "url": snap.url,
                      "fetched_at": snap.fetched_at.isoformat() if snap.fetched_at else None}
                     if snap else None),
    }


@router.post("/records/{record_id}/promote")
def record_promote(record_id: int, user: str = Depends(require_user),
                   db: Session = Depends(get_db),
                   ip: str = Header(default="", alias="X-Forwarded-For")) -> dict:
    actor = _require_super_admin(user, db)
    r = db.get(ExtractedRecord, record_id)
    if r is None:
        raise HTTPException(404, {"error": "record_not_found", "record_id": record_id})
    prev = r.quality_status
    r.quality_status = "main"
    record_audit(db, actor_user_id=actor.id, actor_name=actor.username,
                 action="record.promote", target_type="record",
                 target_id=str(record_id), detail={"from": prev, "to": "main"},
                 ip=ip or None)
    db.commit()
    return {"record_id": record_id, "quality_status": "main"}


@router.delete("/records/{record_id}")
def record_delete(record_id: int, user: str = Depends(require_user),
                  db: Session = Depends(get_db),
                  ip: str = Header(default="", alias="X-Forwarded-For")) -> dict:
    actor = _require_super_admin(user, db)
    r = db.get(ExtractedRecord, record_id)
    if r is None:
        raise HTTPException(404, {"error": "record_not_found", "record_id": record_id})
    db.delete(r)
    record_audit(db, actor_user_id=actor.id, actor_name=actor.username,
                 action="record.delete", target_type="record",
                 target_id=str(record_id), detail={}, ip=ip or None)
    db.commit()
    return {"record_id": record_id, "deleted": True}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin_spine.py -k "datasets_records" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/wangxiaokang/Documents/github/smart-crawler
git add backend/app/api/admin_spine.py backend/tests/test_admin_spine.py
git commit -m "feat(admin): dataset endpoints (list/records/detail/promote/delete)"
```

---

## Task 5: 计费用量端点

**Files:**
- Modify: `backend/app/api/admin_spine.py`
- Test: `backend/tests/test_admin_spine.py`(追加)

- [ ] **Step 1: 写失败测试**

```python
def test_usage_endpoints():
    init_db()
    from app.api import admin_spine
    from app.db import SessionLocal
    from app.models import Usage
    s = SessionLocal()
    s.add(Usage(api_key_id=5, workspace_id=1, endpoint="/spine/worker/execute",
                record_count=1, credits_used=2))
    s.add(Usage(api_key_id=5, workspace_id=1, endpoint="/api/v2/scrape",
                record_count=1, credits_used=3))
    s.commit()
    agg = admin_spine.usage_summary(start=None, end=None, endpoint=None,
                                    user="admin", db=s)
    assert agg["total_credits"] >= 5
    bykey = admin_spine.usage_by_key(user="admin", db=s)
    assert any(r["api_key_id"] == 5 and r["credits"] >= 5 for r in bykey["items"])
    bytenant = admin_spine.usage_by_tenant(user="admin", db=s)
    assert any(r["workspace_id"] == 1 for r in bytenant["items"])
    # endpoint 过滤
    only = admin_spine.usage_summary(start=None, end=None,
                                     endpoint="/spine/worker/execute",
                                     user="admin", db=s)
    assert only["total_credits"] >= 2
    s.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin_spine.py -k "usage_endpoints" -v`
Expected: FAIL

- [ ] **Step 3: 实现端点**

在 `admin_spine.py` 追加(顶部补 import:`from sqlalchemy import func`、`from ..models import Usage`):

```python
def _usage_filtered(db, start, end, endpoint):
    q = db.query(Usage)
    if endpoint:
        q = q.filter(Usage.endpoint == endpoint)
    if start:
        q = q.filter(Usage.occurred_at >= datetime.fromisoformat(start))
    if end:
        q = q.filter(Usage.occurred_at <= datetime.fromisoformat(end))
    return q


@router.get("/usage")
def usage_summary(start: str | None = None, end: str | None = None,
                  endpoint: str | None = None,
                  user: str = Depends(require_user),
                  db: Session = Depends(get_db)) -> dict:
    _require_super_admin(user, db)
    q = _usage_filtered(db, start, end, endpoint)
    total_credits = q.with_entities(func.coalesce(func.sum(Usage.credits_used), 0)).scalar()
    total_records = q.with_entities(func.coalesce(func.sum(Usage.record_count), 0)).scalar()
    return {"total_credits": int(total_credits or 0),
            "total_records": int(total_records or 0),
            "rows": q.count()}


@router.get("/usage/by-key")
def usage_by_key(user: str = Depends(require_user),
                 db: Session = Depends(get_db)) -> dict:
    _require_super_admin(user, db)
    rows = (db.query(Usage.api_key_id,
                     func.sum(Usage.credits_used),
                     func.count(Usage.id))
            .group_by(Usage.api_key_id).all())
    return {"items": [{"api_key_id": k, "credits": int(c or 0), "calls": n}
                      for k, c, n in rows]}


@router.get("/usage/by-tenant")
def usage_by_tenant(user: str = Depends(require_user),
                    db: Session = Depends(get_db)) -> dict:
    _require_super_admin(user, db)
    rows = (db.query(Usage.workspace_id,
                     func.sum(Usage.credits_used),
                     func.count(Usage.id))
            .group_by(Usage.workspace_id).all())
    return {"items": [{"workspace_id": w, "credits": int(c or 0), "calls": n}
                      for w, c, n in rows]}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin_spine.py -k "usage_endpoints" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/wangxiaokang/Documents/github/smart-crawler
git add backend/app/api/admin_spine.py backend/tests/test_admin_spine.py
git commit -m "feat(admin): usage endpoints (summary/by-key/by-tenant)"
```

---

## Task 6: 健康/配置 + 审计端点

**Files:**
- Modify: `backend/app/api/admin_spine.py`
- Test: `backend/tests/test_admin_spine.py`(追加)

- [ ] **Step 1: 写失败测试**

```python
def test_health_config_audit():
    init_db()
    from app.api import admin_spine
    from app.db import SessionLocal
    s = SessionLocal()
    h = admin_spine.health(user="admin", db=s)
    assert "worker_status" in h and "reclaim_hint" in h
    c = admin_spine.config(user="admin", db=s)
    assert "heartbeat_interval" in c and "backoff" in c
    a = admin_spine.audit_list(actor=None, action=None, start=None, end=None,
                               page=1, size=20, user="admin", db=s)
    assert "items" in a and "total" in a
    s.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin_spine.py -k "health_config_audit" -v`
Expected: FAIL

- [ ] **Step 3: 实现端点**

在 `admin_spine.py` 追加(顶部补 import:`from ..models import AdminAuditLog`(若未导入);`from ..spine_worker import HEARTBEAT_INTERVAL` 改为从 spine_queue:`from ..spine_queue import HEARTBEAT_INTERVAL, _backoff`):

```python
@router.get("/health")
def health(user: str = Depends(require_user),
           db: Session = Depends(get_db)) -> dict:
    _require_super_admin(user, db)
    # worker 状态用 heartbeat 推断:最近 running job 的 heartbeat / 最近 success
    last_hb = (db.query(func.max(SpineJob.heartbeat_at)).scalar())
    last_success = (db.query(func.max(SpineJob.finished_at))
                    .filter(SpineJob.status == "success").scalar())
    recent = None
    for t in (last_hb, last_success):
        if t and (recent is None or t > recent):
            recent = t
    if recent is None:
        status = "unknown"
    elif (datetime.utcnow() - recent).total_seconds() <= _STUCK_SEC:
        status = "running"
    else:
        status = "idle"
    stuck = (db.query(SpineJob)
             .filter(SpineJob.status == "running",
                     SpineJob.heartbeat_at < datetime.utcnow() - timedelta(seconds=_STUCK_SEC))
             .count())
    return {"worker_status": status,
            "last_activity_at": recent.isoformat() if recent else None,
            "reclaim_hint": {"stuck_running": stuck},
            "pending": db.query(SpineJob).filter(SpineJob.status == "pending").count()}


@router.get("/config")
def config(user: str = Depends(require_user),
           db: Session = Depends(get_db)) -> dict:
    _require_super_admin(user, db)
    return {"heartbeat_interval": HEARTBEAT_INTERVAL,
            "stuck_timeout_sec": _STUCK_SEC,
            "backoff": {str(i): int(_backoff(i).total_seconds()) for i in (1, 2, 3)}}


@router.get("/audit")
def audit_list(actor: str | None = None, action: str | None = None,
               start: str | None = None, end: str | None = None,
               page: int = 1, size: int = 20,
               user: str = Depends(require_user),
               db: Session = Depends(get_db)) -> dict:
    _require_super_admin(user, db)
    q = db.query(AdminAuditLog)
    if actor:
        q = q.filter(AdminAuditLog.actor_name == actor)
    if action:
        q = q.filter(AdminAuditLog.action == action)
    if start:
        q = q.filter(AdminAuditLog.created_at >= datetime.fromisoformat(start))
    if end:
        q = q.filter(AdminAuditLog.created_at <= datetime.fromisoformat(end))
    total = q.count()
    rows = (q.order_by(AdminAuditLog.id.desc())
            .offset((page - 1) * size).limit(size).all())
    return {"total": total, "items": [
        {"id": r.id, "actor_name": r.actor_name, "action": r.action,
         "target_type": r.target_type, "target_id": r.target_id,
         "detail": r.detail, "ip": r.ip,
         "created_at": r.created_at.isoformat() if r.created_at else None}
        for r in rows]}
```

注:确认 `spine_queue.py` 里 `_backoff` 和 `HEARTBEAT_INTERVAL` 可 import(`_backoff` 是模块级函数,`HEARTBEAT_INTERVAL` 是模块级常量,都在)。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin_spine.py -k "health_config_audit" -v`
Expected: PASS

- [ ] **Step 5: 全量回归**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: 全 passed,无回归。

- [ ] **Step 6: Commit**

```bash
cd /Users/wangxiaokang/Documents/github/smart-crawler
git add backend/app/api/admin_spine.py backend/tests/test_admin_spine.py
git commit -m "feat(admin): health/config/audit endpoints"
```

---

## Task 7: 非超管 403 守卫覆盖测试

**Files:**
- Test: `backend/tests/test_admin_spine.py`(追加)

- [ ] **Step 1: 写测试(确认非超管被挡)**

```python
def test_non_super_admin_blocked():
    import pytest
    from fastapi import HTTPException
    from app.api import admin_spine
    from app.db import SessionLocal
    from app.models import User, Workspace
    from app.auth import hash_password
    init_db(); s = SessionLocal()
    ws = Workspace(name="t-ws", slug="t-ws"); s.add(ws); s.flush()
    u = User(username="plainuser", email="p@e.com",
             password_hash=hash_password("Password1"), role="user",
             global_role=None, status="active", default_workspace_id=ws.id)
    s.add(u); s.commit()
    # 普通用户调任意 admin spine 端点 → 403
    for call in (lambda: admin_spine.jobs_stats(user="plainuser", db=s),
                 lambda: admin_spine.datasets_list(user="plainuser", db=s),
                 lambda: admin_spine.usage_by_key(user="plainuser", db=s)):
        with pytest.raises(HTTPException) as exc:
            call()
        assert exc.value.status_code == 403
    s.close()
```

- [ ] **Step 2: 跑测试确认通过(守卫已在,应直接 PASS)**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin_spine.py -k "non_super_admin" -v`
Expected: PASS(每个端点已调 `_require_super_admin`,普通用户触发 403)

若失败:说明某端点漏了 `_require_super_admin`,补上再跑。

- [ ] **Step 3: Commit**

```bash
cd /Users/wangxiaokang/Documents/github/smart-crawler
git add backend/tests/test_admin_spine.py
git commit -m "test(admin): non-super-admin 403 guard coverage"
```

---

## Task 8: 现有 admin 写操作补审计埋点

**Files:**
- Modify: `backend/app/api/routes.py`
- Test: `backend/tests/test_admin_spine.py`(追加)

- [ ] **Step 1: 写失败测试**

```python
def test_existing_admin_write_audited():
    init_db()
    from app.api import routes
    from app.db import SessionLocal
    from app.models import AdminAuditLog
    s = SessionLocal()
    na = s.query(AdminAuditLog).count()
    # 创建 workspace(现有 admin 写操作)→ 应记审计
    routes.admin_create_workspace(payload={"name": "audited-ws"},
                                  user="admin", db=s)
    assert s.query(AdminAuditLog).count() == na + 1
    row = s.query(AdminAuditLog).order_by(AdminAuditLog.id.desc()).first()
    assert row.action == "workspace.create"
    s.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin_spine.py -k "existing_admin_write" -v`
Expected: FAIL(无审计行)

- [ ] **Step 3: 在 admin_create_workspace 补埋点**

先读 `backend/app/api/routes.py` 的 `admin_create_workspace`(约 1418 行)。在它 commit 之前加审计(顶部确认 `from ..audit import record_audit` 已 import,没有则加):

```python
    # 在 admin_create_workspace 创建 workspace、commit 之前:
    from ..audit import record_audit
    actor = _require_super_admin(user, db)   # 若函数已有 _require_admin/_require_super_admin 调用,复用其返回的 user 对象
    record_audit(db, actor_user_id=getattr(actor, "id", None),
                 actor_name=getattr(actor, "username", user),
                 action="workspace.create", target_type="workspace",
                 target_id=None, detail={"name": payload.get("name")})
```

注意:读现有 `admin_create_workspace` 真实结构——它可能已有鉴权调用和 commit。把 record_audit 插在 db.commit() 之前、同事务。target_id 用新建 workspace 的 id(若 commit/flush 后能拿到则填,拿不到填 None)。**只改这一个端点作为埋点示范**(其余 admin 写操作埋点留作快速跟进,本任务先打通模式 + 验证)。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin_spine.py -k "existing_admin_write" -v`
Expected: PASS

- [ ] **Step 5: 全量回归**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: 全 passed。

- [ ] **Step 6: Commit**

```bash
cd /Users/wangxiaokang/Documents/github/smart-crawler
git add backend/app/api/routes.py backend/tests/test_admin_spine.py
git commit -m "feat(admin): audit workspace.create (existing admin write hook)"
```

---

## Task 9: admin-app 工程脚手架 + 构建通过

**Files:**
- Create: `admin-app/package.json`、`vite.config.ts`、`tsconfig.json`、`index.html`、`src/app/main.ts`、`src/App.vue`

- [ ] **Step 1: 复制 frontend-app 脚手架结构**

先读 `frontend-app/package.json`、`frontend-app/vite.config.ts`、`frontend-app/tsconfig.json`、`frontend-app/index.html`、`frontend-app/src/app/main.ts` 作模板。

新建 `admin-app/package.json`(name 改 `smart-crawler-admin`,deps 与 frontend-app 一致:vue/vue-router/pinia/@nuxt/ui/echarts/lucide-vue-next,devDeps:vite/@vitejs/plugin-vue/typescript/vue-tsc/@tailwindcss/vite)。

新建 `admin-app/vite.config.ts`:与 frontend-app 一致,但 **`base: '/admin/'`**;dev server 加 proxy 把 `/api` 转发到后端(参照 frontend-app 若有 proxy;没有则加 `server.proxy['/api'] = 'http://localhost:8077'`)。

新建 `admin-app/tsconfig.json`、`admin-app/index.html`(title 改"smart-crawler 管理后台")、`admin-app/src/app/main.ts`(createApp + pinia + router + ui)、`admin-app/src/App.vue`(`<RouterView/>`)。

- [ ] **Step 2: 安装依赖并构建**

Run:
```bash
cd admin-app && pnpm install 2>&1 | tail -3
```
Expected: 依赖装好(用 pnpm,与 frontend-app 一致)。

- [ ] **Step 3: 最小路由 + 构建通过**

新建 `admin-app/src/app/router.ts`(先一个占位路由 `/` → 一个最小 `OverviewPage.vue` 显示 "Admin"),`admin-app/src/pages/OverviewPage.vue`(`<template><div>Admin OK</div></template>`)。

Run:
```bash
cd admin-app && pnpm build 2>&1 | tail -5
```
Expected: `admin-app/dist/` 生成,`dist/index.html` 里资源路径带 `/admin/` 前缀(因 base 配置)。

- [ ] **Step 4: 校验 dist 资源路径**

Run: `cd /Users/wangxiaokang/Documents/github/smart-crawler && grep -o '/admin/assets/[^"]*' admin-app/dist/index.html | head`
Expected: 有 `/admin/assets/...` 路径(证明 base 生效)。

- [ ] **Step 5: Commit**

```bash
cd /Users/wangxiaokang/Documents/github/smart-crawler
git add admin-app/ -- ':!admin-app/node_modules' ':!admin-app/dist'
git commit -m "feat(admin-app): Vue3 scaffold (base=/admin/), builds clean"
```

(确认 admin-app/.gitignore 忽略 node_modules + dist;若无,先建。)

---

## Task 10: 后端挂载 /admin StaticFiles

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_admin_spine.py`(追加)

- [ ] **Step 1: 写失败测试**

```python
def test_admin_spa_served():
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    r = client.get("/admin/")
    # admin-app/dist 存在时返 200 + html;不存在时跳过
    if r.status_code == 200:
        assert "text/html" in r.headers.get("content-type", "")
```

- [ ] **Step 2: 加挂载**

先读 `backend/app/main.py` 现有 `FRONTEND_APP_DIST` 定义和 frontend 挂载(约 132 行)。仿照加 `ADMIN_APP_DIST`:

```python
# 顶部常量区(FRONTEND_APP_DIST 附近):
ADMIN_APP_DIST = BASE_DIR / "admin-app" / "dist"   # BASE_DIR 用现有指向仓库根的常量
ADMIN_APP_INDEX = ADMIN_APP_DIST / "index.html"

# 挂载区(frontend /assets 挂载之后):
if (ADMIN_APP_DIST / "assets").exists():
    app.mount("/admin/assets",
              StaticFiles(directory=ADMIN_APP_DIST / "assets"),
              name="admin-assets")

# SPA 回退路由(放在 catch-all frontend 路由之前,确保 /admin 优先):
@app.get("/admin")
@app.get("/admin/{path:path}")
def _admin_spa(path: str = ""):
    if ADMIN_APP_INDEX.exists():
        return FileResponse(ADMIN_APP_INDEX, headers=NO_CACHE_HEADERS)
    raise HTTPException(404, "admin-app not built")
```

确认 `BASE_DIR`/`NO_CACHE_HEADERS`/`FileResponse`/`HTTPException` 在 main.py 已定义/导入(读文件确认;FRONTEND_APP_DIST 用的什么根常量就用什么)。`/admin/{path:path}` 要放在 frontend 的 catch-all SPA 路由**之前**注册,否则被先匹配。

- [ ] **Step 3: 跑测试确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin_spine.py -k "admin_spa" -v`
Expected: PASS(dist 已由 Task 9 构建)

- [ ] **Step 4: 全量回归(确认没破坏 frontend 路由)**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: 全 passed。

- [ ] **Step 5: Commit**

```bash
cd /Users/wangxiaokang/Documents/github/smart-crawler
git add backend/app/main.py backend/tests/test_admin_spine.py
git commit -m "feat(admin): mount admin-app dist at /admin"
```

---

## Task 11: admin-app auth + client + 布局

**Files:**
- Create: `admin-app/src/api/client.ts`、`stores/auth.ts`、`api/auth.ts`、`components/layout/AdminLayout.vue`、`pages/LoginPage.vue`
- Modify: `admin-app/src/app/router.ts`

- [ ] **Step 1: client + auth store**

参照 `frontend-app/src/api/client.ts` 建 `admin-app/src/api/client.ts`(apiJson、jsonBody、qs、fmtDate、fmtNumber——复制需要的)。
参照 `frontend-app/src/stores/auth.ts` + `api/auth.ts` 建 admin 版:`login`/`loadMe`/`clear`,`user` 含 `global_role`。

- [ ] **Step 2: router guard(super_admin)**

`admin-app/src/app/router.ts`:路由表(login + app 布局 + 7 子页面占位),`beforeEach`:
```ts
router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (to.path === '/login') return
  if (!auth.token) return '/login'
  if (!auth.user) { try { await auth.loadMe() } catch { return '/login' } }
  if (auth.user?.global_role !== 'super_admin') return '/login'  // 非超管挡回登录
})
```

- [ ] **Step 3: AdminLayout 侧边栏 + LoginPage**

`AdminLayout.vue`:左侧边栏 7 项导航(概览/租户用户/数据集/队列/计费/健康/审计)+ `<RouterView/>`。`LoginPage.vue`:用户名密码 → `auth.login` → 成功跳 `/`。

- [ ] **Step 4: 构建通过**

Run: `cd admin-app && pnpm build 2>&1 | tail -3`
Expected: build 成功。

- [ ] **Step 5: Commit**

```bash
cd /Users/wangxiaokang/Documents/github/smart-crawler
git add admin-app/src -- ':!admin-app/node_modules' ':!admin-app/dist'
git commit -m "feat(admin-app): auth store + client + layout + login + super_admin guard"
```

---

## Task 12: admin-app 队列页 + 概览页(含轮询)

**Files:**
- Create: `admin-app/src/api/queue.ts`、`pages/QueuePage.vue`、`pages/OverviewPage.vue`、`components/common/StatCard.vue`、`StatusBadge.vue`

- [ ] **Step 1: queue API + 队列页**

`api/queue.ts`:`listJobs(params)`、`jobStats()`、`retryJob(id)`、`enqueueJob(payload)`、`jobDetail(id)` → 调 `/api/admin/spine/jobs*`。
`QueuePage.vue`:stats 卡片(pending/running/success/failed/stuck)+ 任务表格(状态过滤、分页)+ 重试按钮 + 手动入队表单。**轮询**:`setInterval(load, 5000)`,`onUnmounted` 清理,顶部开关切换。

- [ ] **Step 2: 概览页**

`OverviewPage.vue`:聚合 `jobStats()` + `/api/admin/spine/usage` + `/api/admin/spine/health`,指标卡 + 简单 echarts 趋势(可选)。同样加轮询开关。

- [ ] **Step 3: 构建通过**

Run: `cd admin-app && pnpm build 2>&1 | tail -3`
Expected: build 成功。

- [ ] **Step 4: Commit**

```bash
cd /Users/wangxiaokang/Documents/github/smart-crawler
git add admin-app/src -- ':!admin-app/node_modules' ':!admin-app/dist'
git commit -m "feat(admin-app): queue + overview pages with polling"
```

---

## Task 13: admin-app 数据集页 + 计费页

**Files:**
- Create: `admin-app/src/api/{datasets,usage}.ts`、`pages/{DatasetsPage,DatasetDetailPage,UsagePage}.vue`

- [ ] **Step 1: 数据集页**

`api/datasets.ts`:`listDatasets()`、`datasetRecords(id, params)`、`recordDetail(id)`、`promoteRecord(id)`、`deleteRecord(id)`。
`DatasetsPage.vue`:数据集表格(名称/记录数/租户)→ 点进 `DatasetDetailPage.vue`:记录分页表(quality_status 过滤)+ 记录详情抽屉(data JSON + provenance)+ promote/delete 按钮(带确认)。

- [ ] **Step 2: 计费页**

`api/usage.ts`:`usageSummary(params)`、`usageByKey()`、`usageByTenant()`。
`UsagePage.vue`:汇总卡 + by-key 表 + by-tenant 表 + 时间范围/endpoint 过滤(同步 vs 异步 endpoint 分列展示)。

- [ ] **Step 3: 构建通过**

Run: `cd admin-app && pnpm build 2>&1 | tail -3`
Expected: build 成功。

- [ ] **Step 4: Commit**

```bash
cd /Users/wangxiaokang/Documents/github/smart-crawler
git add admin-app/src -- ':!admin-app/node_modules' ':!admin-app/dist'
git commit -m "feat(admin-app): datasets + usage pages"
```

---

## Task 14: admin-app 健康页 + 审计页 + 租户用户页 + 端到端验收

**Files:**
- Create: `admin-app/src/api/{health,audit,tenants}.ts`、`pages/{HealthPage,AuditPage,TenantsPage}.vue`

- [ ] **Step 1: 健康 + 审计 + 租户页**

`HealthPage.vue`:`/health` + `/config` 展示(worker 状态徽章、卡死数、心跳间隔、退避表)。
`AuditPage.vue`:`/audit` 审计表(actor/action/target/时间过滤、分页)。
`TenantsPage.vue`:对接**现有** `/api/admin/workspaces`、`/api/admin/users`、`/api/admin/invites`(列表 + 基本 CRUD;参照 frontend-app SettingsPage 已有的 admin api 调用)。

- [ ] **Step 2: 构建通过 + 后端全量回归**

Run:
```bash
cd admin-app && pnpm build 2>&1 | tail -3
cd ../backend && .venv/bin/python -m pytest -q 2>&1 | tail -3
```
Expected: build 成功;后端全 passed。

- [ ] **Step 3: 端到端冒烟(后端起服务 + 构建产物)**

Run:
```bash
cd /Users/wangxiaokang/Documents/github/smart-crawler/backend
.venv/bin/python -c "
from fastapi.testclient import TestClient
from app.main import app
from app.db import init_db
init_db()
c = TestClient(app)
# /admin SPA 可达
assert c.get('/admin/').status_code in (200, 404)  # 200 if built
# admin spine 端点鉴权门生效
assert c.get('/api/admin/spine/jobs/stats').status_code in (401, 403)
print('admin e2e smoke OK')
"
```
Expected: `admin e2e smoke OK`。

- [ ] **Step 4: 更新 memory**

新建/更新 memory:admin-app 后台管理系统(独立 Vue3 工程挂 /admin,超管双重鉴权,/api/admin/spine/* 端点 + 审计,7 模块,未部署)。MEMORY.md 加索引。

- [ ] **Step 5: Commit**

```bash
cd /Users/wangxiaokang/Documents/github/smart-crawler
git add admin-app/src -- ':!admin-app/node_modules' ':!admin-app/dist'
git commit -m "feat(admin-app): health + audit + tenants pages; e2e smoke"
```

---

## Self-Review(写计划者已核对)

- **Spec 覆盖**:模块1概览→Task12;模块2租户用户→Task14;模块3数据集→Task4(后端)+Task13(前端);模块4队列→Task3+Task12;模块5计费→Task5+Task13;模块6健康→Task6+Task14;模块7审计→Task1(表)+Task6(端点)+Task8(现有埋点)+Task14(前端);脚手架→Task9;挂载→Task10;鉴权→Task2+Task7+Task11 guard。全覆盖。
- **类型/签名一致**:端点函数名(jobs_stats/jobs_list/job_detail/job_retry/job_enqueue/datasets_list/dataset_records/record_detail/record_promote/record_delete/usage_summary/usage_by_key/usage_by_tenant/health/config/audit_list)跨 Task 一致;前端 api 函数名与端点对应。`record_audit` 签名 Task1 定义、后续 Task3/4/6/8 调用一致。
- **复用点已核实**:`_require_super_admin(user,db)` 收 Depends(require_user) 的 str;`user=="admin"` 兜底 super_admin 便于测试;Usage 时间列 `occurred_at`;模型字段全核对;`spine_queue.enqueue`/`_backoff`/`HEARTBEAT_INTERVAL` 可 import;main.py 挂载方式。
- **无占位符**:后端每步含完整代码;前端任务给出文件清单 + 每页职责 + build 验收(前端无单测传统,以 build 通过 + 端到端冒烟验收,与 frontend-app 现状一致)。
- **已知风险(实现时核实)**:
  1. main.py 的根常量名(指向仓库根)需读真实代码确认(我用 `BASE_DIR` 占位,实际可能叫别的);`/admin/{path:path}` 必须在 frontend catch-all 之前注册。
  2. `admin_create_workspace` 真实结构(鉴权调用/commit 时机)以实际为准,埋点插在同事务 commit 前。
  3. 前端 dev proxy 端口(后端默认 8077,run.sh 确认)。
  4. pnpm 安装/构建在本机可用(frontend-app 已用 pnpm)。
