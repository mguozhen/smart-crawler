# 租户 Webhook 通知 · 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让每个租户(workspace)配置自己的 webhook 地址,三个任务队列(spine/crawl/ondemand)进入终态时,服务端带 HMAC 签名 POST 推送瘦载荷通知,投递经 webhook_deliveries 表 + spine_worker 扫描重试保证不丢。

**Architecture:** 任务终态点只在事务内 INSERT 一条 pending delivery(快、容错,绝不阻断任务);独立的 `webhooks.py` 负责登记/投递/签名/SSRF 校验;`spine_worker.run_loop` 每轮调 `dispatch_pending()` 异步发出。配置经 REST API + 控制台 UI 管理。

**Tech Stack:** FastAPI + SQLAlchemy 2.0 + PostgreSQL/SQLite,`requests`(已依赖)发 POST,`hmac`/`hashlib`(标准库)签名,pytest + TestClient。

> **设计来源:** `docs/superpowers/specs/2026-06-12-tenant-webhook-notifications-design.md`
> **分支:** `feat/tenant-webhook-notifications`(已创建,spec 已提交)

---

## 文件结构

**新增**
- `backend/app/webhooks.py` — 核心:`enqueue_delivery` / `dispatch_pending` / `sign_payload` / `validate_webhook_url` / `build_payload`
- `backend/app/api/webhook.py` — 6 个 REST 端点(挂在 v2 router 下)
- `backend/tests/test_webhooks.py` — 单元 + API 测试

**改动**
- `backend/app/models.py` — 新增 `WebhookConfig` / `WebhookDelivery`
- `backend/app/spine_queue.py` — `execute_job` 成功/失败终态后 `enqueue_delivery`
- `backend/app/runner.py` — `execute_job` success/failed/blocked 终态后 `enqueue_delivery`
- `backend/app/ondemand/queue.py` — `process_one` 终态后 `enqueue_delivery`
- `backend/app/spine_worker.py` — `run_loop` 每轮调 `dispatch_pending()`
- `backend/app/main.py` — 注册 webhook router
- `frontend/<console index.html>` — Webhook 配置卡片

---

## Task 1: 数据模型 — WebhookConfig / WebhookDelivery

**Files:**
- Modify: `backend/app/models.py`(文件末尾追加两个类)
- Test: `backend/tests/test_webhooks.py`

- [ ] **Step 1: 写失败测试 — 表与列存在**

新建 `backend/tests/test_webhooks.py`:

```python
"""租户 webhook 通知测试。"""
from datetime import datetime

from sqlalchemy import inspect

from app.db import SessionLocal, engine, init_db


def test_webhook_tables_exist():
    init_db()
    insp = inspect(engine)
    assert insp.has_table("webhook_configs"), "缺表 webhook_configs"
    assert insp.has_table("webhook_deliveries"), "缺表 webhook_deliveries"
    cfg_cols = {c["name"] for c in insp.get_columns("webhook_configs")}
    for c in ("id", "workspace_id", "url", "secret", "active",
              "created_at", "updated_at"):
        assert c in cfg_cols, f"webhook_configs 缺列 {c}"
    del_cols = {c["name"] for c in insp.get_columns("webhook_deliveries")}
    for c in ("id", "workspace_id", "config_id", "event_type", "job_kind",
              "job_id", "payload", "status", "retries", "max_retries",
              "next_retry_at", "http_status", "response_snippet",
              "created_at", "finished_at"):
        assert c in del_cols, f"webhook_deliveries 缺列 {c}"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_webhooks.py::test_webhook_tables_exist -v`
Expected: FAIL — `缺表 webhook_configs`(表尚未定义)

- [ ] **Step 3: 在 models.py 末尾追加两个模型**

先确认 `backend/app/models.py` 顶部已 import:`Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text`(已有,沿用)。文件末尾追加:

```python
class WebhookConfig(Base):
    """每个 workspace 一条 webhook 配置。任务终态时向 url POST 通知。"""

    __tablename__ = "webhook_configs"

    id = Column(Integer, primary_key=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"),
                          unique=True, index=True)
    url = Column(String, nullable=False)            # 目标地址(http/https)
    secret = Column(String, nullable=False)         # HMAC-SHA256 密钥(明文存,签名需原文)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class WebhookDelivery(Base):
    """一次 webhook 投递记录。pending→success/failed,失败按退避重试。"""

    __tablename__ = "webhook_deliveries"

    id = Column(Integer, primary_key=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), index=True)
    config_id = Column(Integer, ForeignKey("webhook_configs.id"), index=True)
    event_type = Column(String)                     # job.success | job.failed
    job_kind = Column(String)                       # spine | crawl | ondemand
    job_id = Column(Integer)
    payload = Column(JSON)                          # 冻结的瘦载荷快照
    status = Column(String, index=True)             # pending | success | failed
    retries = Column(Integer, default=0)
    max_retries = Column(Integer, default=5)
    next_retry_at = Column(DateTime, index=True)
    http_status = Column(Integer)                   # 末次响应码
    response_snippet = Column(Text)                 # 末次响应体前 500 字
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    finished_at = Column(DateTime)
```

> `init_db()` 的 `_migrate()` 会自动 `CREATE TABLE` 新表 + `ALTER` 补列,无需手写迁移。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_webhooks.py::test_webhook_tables_exist -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/models.py backend/tests/test_webhooks.py
git commit -m "feat(webhook): WebhookConfig/WebhookDelivery 数据模型

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: 载荷构造 — build_payload

**Files:**
- Create: `backend/app/webhooks.py`
- Test: `backend/tests/test_webhooks.py`

- [ ] **Step 1: 写失败测试 — 三队列载荷形状**

追加到 `backend/tests/test_webhooks.py`:

```python
def test_build_payload_spine():
    from app.webhooks import build_payload
    p = build_payload(
        delivery_id=12345, workspace_id=42, job_kind="spine", job_id=9876,
        status="success", error=None,
        created_at=datetime(2026, 6, 12, 8, 29, 40),
        finished_at=datetime(2026, 6, 12, 8, 30, 0),
        result={"record_id": 555, "url": "https://x.com/p/1", "dataset": "ds"})
    assert p["event"] == "job.success"
    assert p["webhook_id"] == "whd_12345"
    assert p["workspace_id"] == 42
    assert p["job"]["id"] == 9876
    assert p["job"]["kind"] == "spine"
    assert p["job"]["status"] == "success"
    assert p["job"]["error"] is None
    assert p["job"]["result"]["record_id"] == 555
    # 时间戳为 ISO 字符串
    assert p["timestamp"].startswith("2026-06-12T08:30:00")
    assert p["job"]["created_at"].startswith("2026-06-12T08:29:40")


def test_build_payload_failed_event_and_error():
    from app.webhooks import build_payload
    p = build_payload(
        delivery_id=7, workspace_id=1, job_kind="crawl", job_id=2,
        status="failed", error="熔断:站点冷却",
        created_at=None, finished_at=None,
        result={"site": "haier-de"})
    assert p["event"] == "job.failed"
    assert p["job"]["error"] == "熔断:站点冷却"
    assert p["job"]["created_at"] is None
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && python -m pytest tests/test_webhooks.py::test_build_payload_spine -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.webhooks'`

- [ ] **Step 3: 创建 webhooks.py 并实现 build_payload**

```python
"""租户 webhook 通知 —— 登记投递 / 异步发出 / HMAC 签名 / SSRF 校验。

任务终态点调 enqueue_delivery() 在事务内登记一条 pending(快、容错);
spine_worker 每轮调 dispatch_pending() 把到期 pending 逐条 POST 出去。
与三个任务队列解耦:dispatch 只读 webhook_deliveries 表。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime
from ipaddress import ip_address
from urllib.parse import urlparse

logger = logging.getLogger("smart-crawler.webhooks")


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def build_payload(*, delivery_id: int, workspace_id: int, job_kind: str,
                  job_id: int, status: str, error: str | None,
                  created_at: datetime | None, finished_at: datetime | None,
                  result: dict) -> dict:
    """构造瘦载荷。event 由 status 推导:success→job.success,其余→job.failed。"""
    event = "job.success" if status == "success" else "job.failed"
    return {
        "event": event,
        "webhook_id": f"whd_{delivery_id}",
        "timestamp": _iso(finished_at) or _iso(datetime.utcnow()),
        "workspace_id": workspace_id,
        "job": {
            "id": job_id,
            "kind": job_kind,
            "status": status,
            "created_at": _iso(created_at),
            "finished_at": _iso(finished_at),
            "error": error,
            "result": result,
        },
    }
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && python -m pytest tests/test_webhooks.py -k build_payload -v`
Expected: 2 passed

- [ ] **Step 5: 提交**

```bash
git add backend/app/webhooks.py backend/tests/test_webhooks.py
git commit -m "feat(webhook): build_payload 瘦载荷构造

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: HMAC 签名 — sign_payload

**Files:**
- Modify: `backend/app/webhooks.py`
- Test: `backend/tests/test_webhooks.py`

- [ ] **Step 1: 写失败测试 — 签名可独立复算**

```python
def test_sign_payload_matches_independent_hmac():
    import hashlib
    import hmac
    from app.webhooks import sign_payload
    secret = "whsec_test123"
    raw_body = b'{"event":"job.success"}'
    sig = sign_payload(secret, raw_body)
    expected = "sha256=" + hmac.new(
        secret.encode(), raw_body, hashlib.sha256).hexdigest()
    assert sig == expected
    assert sig.startswith("sha256=")
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && python -m pytest tests/test_webhooks.py::test_sign_payload_matches_independent_hmac -v`
Expected: FAIL — `cannot import name 'sign_payload'`

- [ ] **Step 3: 在 webhooks.py 追加 sign_payload**

```python
def sign_payload(secret: str, raw_body: bytes) -> str:
    """对原始请求体算 HMAC-SHA256,返回 'sha256=<hex>'。租户用同 secret 验签。"""
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && python -m pytest tests/test_webhooks.py::test_sign_payload_matches_independent_hmac -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/webhooks.py backend/tests/test_webhooks.py
git commit -m "feat(webhook): HMAC-SHA256 签名

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: SSRF URL 校验 — validate_webhook_url

**Files:**
- Modify: `backend/app/webhooks.py`
- Test: `backend/tests/test_webhooks.py`

- [ ] **Step 1: 写失败测试 — 内网/非http被拒,公网通过**

```python
import pytest


def test_validate_webhook_url_rejects_private_and_nonhttp():
    from app.webhooks import WebhookUrlError, validate_webhook_url
    for bad in ("http://localhost/hook", "http://127.0.0.1/hook",
                "http://10.0.0.5/hook", "http://192.168.1.1/hook",
                "http://172.16.0.1/hook", "http://169.254.169.254/hook",
                "ftp://example.com/hook", "not-a-url"):
        with pytest.raises(WebhookUrlError):
            validate_webhook_url(bad)


def test_validate_webhook_url_accepts_public_https():
    from app.webhooks import validate_webhook_url
    # 不抛即通过
    validate_webhook_url("https://hooks.example.com/ingest")
    validate_webhook_url("http://203.0.113.10/hook")
```

> 注:校验用主机名字面量判断(IP 字面量 + localhost),不做 DNS 解析——spec 第 8 节明确 DNS rebinding 深防留待后续。

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && python -m pytest tests/test_webhooks.py -k validate_webhook_url -v`
Expected: FAIL — `cannot import name 'WebhookUrlError'`

- [ ] **Step 3: 在 webhooks.py 追加校验**

```python
class WebhookUrlError(ValueError):
    """webhook URL 不合法(非 http(s) / 指向内网 / 无法解析)。"""


def _is_private_host(host: str) -> bool:
    """host 是内网/环回/链路本地 → True。非 IP 字面量(域名)按公网放行。"""
    if host in ("localhost", "localhost.localdomain"):
        return True
    try:
        ip = ip_address(host)
    except ValueError:
        return False  # 域名:此处不解析,放行(DNS rebinding 深防留待后续)
    return (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def validate_webhook_url(url: str) -> None:
    """校验租户填的 webhook URL。不合法抛 WebhookUrlError。"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise WebhookUrlError("webhook URL 必须是 http 或 https")
    host = parsed.hostname
    if not host:
        raise WebhookUrlError("webhook URL 缺少主机名")
    if _is_private_host(host):
        raise WebhookUrlError(f"webhook URL 不能指向内网/环回地址: {host}")
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && python -m pytest tests/test_webhooks.py -k validate_webhook_url -v`
Expected: 2 passed

- [ ] **Step 5: 提交**

```bash
git add backend/app/webhooks.py backend/tests/test_webhooks.py
git commit -m "feat(webhook): SSRF URL 校验(拒内网/非http)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: 登记投递 — enqueue_delivery

**Files:**
- Modify: `backend/app/webhooks.py`
- Test: `backend/tests/test_webhooks.py`

- [ ] **Step 1: 写失败测试 — 有配置插一条,无配置不插**

```python
def _make_active_config(workspace_id: int, url="https://hooks.example.com/x"):
    """造一条 active webhook 配置,返回 config_id。"""
    from app.db import SessionLocal
    from app.models import WebhookConfig
    s = SessionLocal()
    try:
        s.query(WebhookConfig).filter(
            WebhookConfig.workspace_id == workspace_id).delete()
        cfg = WebhookConfig(workspace_id=workspace_id, url=url,
                            secret="whsec_x", active=True)
        s.add(cfg)
        s.commit()
        return cfg.id
    finally:
        s.close()


def test_enqueue_delivery_inserts_pending_when_config_active():
    from app.db import SessionLocal
    from app.models import WebhookDelivery
    from app.webhooks import enqueue_delivery
    init_db()
    ws = 4242
    _make_active_config(ws)
    s = SessionLocal()
    try:
        enqueue_delivery(s, workspace_id=ws, job_kind="spine", job_id=111,
                         status="success", error=None,
                         created_at=None, finished_at=None,
                         result={"record_id": 9})
        s.commit()
        rows = s.query(WebhookDelivery).filter(
            WebhookDelivery.workspace_id == ws,
            WebhookDelivery.job_id == 111).all()
        assert len(rows) == 1
        d = rows[0]
        assert d.status == "pending"
        assert d.job_kind == "spine"
        assert d.event_type == "job.success"
        assert d.next_retry_at is not None
        assert d.payload["webhook_id"] == f"whd_{d.id}"
        assert d.payload["job"]["result"]["record_id"] == 9
    finally:
        s.close()


def test_enqueue_delivery_skips_when_no_config():
    from app.db import SessionLocal
    from app.models import WebhookConfig, WebhookDelivery
    from app.webhooks import enqueue_delivery
    init_db()
    ws = 4343
    s = SessionLocal()
    try:
        s.query(WebhookConfig).filter(
            WebhookConfig.workspace_id == ws).delete()
        s.commit()
        enqueue_delivery(s, workspace_id=ws, job_kind="crawl", job_id=222,
                         status="failed", error="x",
                         created_at=None, finished_at=None, result={})
        s.commit()
        rows = s.query(WebhookDelivery).filter(
            WebhookDelivery.workspace_id == ws).all()
        assert rows == []
    finally:
        s.close()


def test_enqueue_delivery_skips_when_inactive():
    from app.db import SessionLocal
    from app.models import WebhookConfig, WebhookDelivery
    from app.webhooks import enqueue_delivery
    init_db()
    ws = 4444
    cfg_id = _make_active_config(ws)
    s = SessionLocal()
    try:
        s.get(WebhookConfig, cfg_id).active = False
        s.commit()
        enqueue_delivery(s, workspace_id=ws, job_kind="spine", job_id=333,
                         status="success", error=None,
                         created_at=None, finished_at=None, result={})
        s.commit()
        assert s.query(WebhookDelivery).filter(
            WebhookDelivery.job_id == 333).all() == []
    finally:
        s.close()


def test_enqueue_delivery_never_raises_on_bad_workspace():
    """workspace_id=None(任务无租户)时静默跳过,绝不抛——不能拖垮任务。"""
    from app.db import SessionLocal
    from app.webhooks import enqueue_delivery
    init_db()
    s = SessionLocal()
    try:
        enqueue_delivery(s, workspace_id=None, job_kind="spine", job_id=1,
                         status="success", error=None,
                         created_at=None, finished_at=None, result={})
        s.commit()  # 不抛即通过
    finally:
        s.close()
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && python -m pytest tests/test_webhooks.py -k enqueue_delivery -v`
Expected: FAIL — `cannot import name 'enqueue_delivery'`

- [ ] **Step 3: 在 webhooks.py 追加 enqueue_delivery**

```python
def enqueue_delivery(db, *, workspace_id, job_kind: str, job_id: int,
                     status: str, error: str | None,
                     created_at, finished_at, result: dict) -> None:
    """在调用方事务内登记一条 pending delivery。

    无 active 配置 / workspace_id 为空 → 静默跳过。任何异常都吞掉并记日志——
    本函数在任务终态点调用,绝不能抛出而拖垮任务落库(镜像 _record_execute_usage)。
    """
    from .models import WebhookConfig, WebhookDelivery
    try:
        if workspace_id is None:
            return
        cfg = (db.query(WebhookConfig)
               .filter(WebhookConfig.workspace_id == workspace_id,
                       WebhookConfig.active.is_(True)).first())
        if cfg is None:
            return
        delivery = WebhookDelivery(
            workspace_id=workspace_id, config_id=cfg.id,
            event_type="job.success" if status == "success" else "job.failed",
            job_kind=job_kind, job_id=job_id, status="pending",
            retries=0, max_retries=5,
            next_retry_at=datetime.utcnow(), created_at=datetime.utcnow())
        db.add(delivery)
        db.flush()  # 拿 delivery.id 填进 payload 的 webhook_id
        delivery.payload = build_payload(
            delivery_id=delivery.id, workspace_id=workspace_id,
            job_kind=job_kind, job_id=job_id, status=status, error=error,
            created_at=created_at, finished_at=finished_at, result=result)
    except Exception as exc:
        logger.error("enqueue_delivery 失败(已吞,不阻断任务): %s", exc)
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && python -m pytest tests/test_webhooks.py -k enqueue_delivery -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add backend/app/webhooks.py backend/tests/test_webhooks.py
git commit -m "feat(webhook): enqueue_delivery 事务内登记 pending(容错不阻断)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: 退避重试 — _backoff

**Files:**
- Modify: `backend/app/webhooks.py`
- Test: `backend/tests/test_webhooks.py`

- [ ] **Step 1: 写失败测试 — 退避表**

```python
def test_webhook_backoff_table():
    from datetime import timedelta
    from app.webhooks import _backoff
    assert _backoff(1) == timedelta(seconds=30)
    assert _backoff(2) == timedelta(seconds=120)
    assert _backoff(3) == timedelta(seconds=600)
    assert _backoff(4) == timedelta(seconds=600)   # 封顶 10m
    assert _backoff(9) == timedelta(seconds=600)
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && python -m pytest tests/test_webhooks.py::test_webhook_backoff_table -v`
Expected: FAIL — `cannot import name '_backoff'`

- [ ] **Step 3: 在 webhooks.py 追加 _backoff**

顶部 import 补 `timedelta`:把 `from datetime import datetime` 改为 `from datetime import datetime, timedelta`。然后追加:

```python
def _backoff(retries: int) -> timedelta:
    """webhook 投递退避,与 spine_queue 同表:1→30s, 2→2m, 3→10m,之后封顶 10m。"""
    table = {1: 30, 2: 120, 3: 600}
    return timedelta(seconds=table.get(retries, 600))
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && python -m pytest tests/test_webhooks.py::test_webhook_backoff_table -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/webhooks.py backend/tests/test_webhooks.py
git commit -m "feat(webhook): 投递退避表(复用 spine 节奏)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: 扫描投递 — dispatch_pending

**Files:**
- Modify: `backend/app/webhooks.py`
- Test: `backend/tests/test_webhooks.py`

- [ ] **Step 1: 写失败测试 — 2xx成功 / 5xx退避重试 / 超限failed**

```python
from unittest.mock import patch


class _Resp:
    def __init__(self, status_code, text="ok"):
        self.status_code = status_code
        self.text = text


def _insert_pending_delivery(workspace_id, job_id, url="https://hooks.example.com/x"):
    """直接插一条到期 pending delivery,返回 delivery_id。"""
    from datetime import datetime
    from app.db import SessionLocal
    from app.models import WebhookConfig, WebhookDelivery
    s = SessionLocal()
    try:
        s.query(WebhookConfig).filter(
            WebhookConfig.workspace_id == workspace_id).delete()
        cfg = WebhookConfig(workspace_id=workspace_id, url=url,
                            secret="whsec_x", active=True)
        s.add(cfg); s.flush()
        d = WebhookDelivery(
            workspace_id=workspace_id, config_id=cfg.id,
            event_type="job.success", job_kind="spine", job_id=job_id,
            status="pending", retries=0, max_retries=5,
            next_retry_at=datetime.utcnow(), created_at=datetime.utcnow(),
            payload={"event": "job.success", "webhook_id": "whd_x"})
        s.add(d); s.commit()
        return d.id
    finally:
        s.close()


def test_dispatch_pending_2xx_marks_success():
    from app.db import SessionLocal
    from app.models import WebhookDelivery
    from app.webhooks import dispatch_pending
    init_db()
    did = _insert_pending_delivery(5001, 600)
    with patch("app.webhooks.requests.post", return_value=_Resp(200)):
        dispatch_pending()
    s = SessionLocal()
    try:
        d = s.get(WebhookDelivery, did)
        assert d.status == "success"
        assert d.http_status == 200
        assert d.finished_at is not None
    finally:
        s.close()


def test_dispatch_pending_5xx_retries_with_backoff():
    from datetime import datetime
    from app.db import SessionLocal
    from app.models import WebhookDelivery
    from app.webhooks import dispatch_pending
    init_db()
    did = _insert_pending_delivery(5002, 601)
    with patch("app.webhooks.requests.post", return_value=_Resp(503, "boom")):
        dispatch_pending()
    s = SessionLocal()
    try:
        d = s.get(WebhookDelivery, did)
        assert d.status == "pending"        # 仍待重试
        assert d.retries == 1
        assert d.http_status == 503
        assert d.next_retry_at > datetime.utcnow()  # 退避推后
    finally:
        s.close()


def test_dispatch_pending_exhausts_to_failed():
    from app.db import SessionLocal
    from app.models import WebhookDelivery
    from app.webhooks import dispatch_pending
    init_db()
    did = _insert_pending_delivery(5003, 602)
    # max_retries=5:连发 5 次 503,第 5 次后应判 failed
    s = SessionLocal()
    s.get(WebhookDelivery, did).retries = 4   # 已重试 4 次,本次是第 5 次
    s.commit(); s.close()
    with patch("app.webhooks.requests.post", return_value=_Resp(500)):
        dispatch_pending()
    s = SessionLocal()
    try:
        d = s.get(WebhookDelivery, did)
        assert d.status == "failed"
        assert d.finished_at is not None
    finally:
        s.close()


def test_dispatch_pending_signs_request():
    """投递时带 X-Webhook-Signature(用 config.secret 对 raw body 签)。"""
    from app.webhooks import dispatch_pending, sign_payload
    init_db()
    _insert_pending_delivery(5004, 603)
    captured = {}

    def _fake_post(url, data=None, headers=None, timeout=None):
        captured["headers"] = headers
        captured["data"] = data
        return _Resp(200)

    with patch("app.webhooks.requests.post", side_effect=_fake_post):
        dispatch_pending()
    assert "X-Webhook-Signature" in captured["headers"]
    expected = sign_payload("whsec_x", captured["data"])
    assert captured["headers"]["X-Webhook-Signature"] == expected
    assert captured["headers"]["X-Webhook-Id"]
```

> 注:测试共享文件 DB,各用例用独立 workspace_id(5001-5004)避免 pending 互串。`dispatch_pending` 须只处理 `next_retry_at <= now` 的 pending。

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && python -m pytest tests/test_webhooks.py -k dispatch_pending -v`
Expected: FAIL — `cannot import name 'dispatch_pending'`

- [ ] **Step 3: 在 webhooks.py 追加 dispatch_pending**

顶部追加 import:`import requests`(已在 requirements,worker 进程可用)。然后:

```python
DELIVERY_TIMEOUT_SEC = 10
DISPATCH_BATCH = 50


def dispatch_pending() -> int:
    """发出到期的 pending delivery,返回本轮处理条数。spine_worker 每轮调一次。

    逐条独立事务,单条异常不影响其它。2xx→success;否则 retries++ 按退避重排,
    超 max_retries→failed。绝不抛出(worker 不能因投递崩)。
    """
    from .db import session_scope
    from .models import WebhookConfig, WebhookDelivery

    processed = 0
    try:
        with session_scope() as s:
            now = datetime.utcnow()
            rows = (s.query(WebhookDelivery)
                    .filter(WebhookDelivery.status == "pending",
                            WebhookDelivery.next_retry_at <= now)
                    .order_by(WebhookDelivery.id)
                    .limit(DISPATCH_BATCH).all())
            ids = [d.id for d in rows]
    except Exception as exc:
        logger.error("dispatch_pending 取队列失败: %s", exc)
        return 0

    for did in ids:
        try:
            _deliver_one(did)
            processed += 1
        except Exception as exc:
            logger.error("投递 delivery %s 异常(已吞): %s", did, exc)
    return processed


def _deliver_one(delivery_id: int) -> None:
    """发单条 delivery。独立事务读配置+载荷,POST,按结果更新状态。"""
    from .db import session_scope
    from .models import WebhookConfig, WebhookDelivery

    with session_scope() as s:
        d = s.get(WebhookDelivery, delivery_id)
        if d is None or d.status != "pending":
            return
        cfg = s.get(WebhookConfig, d.config_id)
        if cfg is None:                      # 配置已删 → 弃投
            d.status = "failed"
            d.response_snippet = "webhook 配置已删除"
            d.finished_at = datetime.utcnow()
            return
        url = cfg.url
        secret = cfg.secret
        raw_body = json.dumps(d.payload, ensure_ascii=False,
                              separators=(",", ":")).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "X-Webhook-Signature": sign_payload(secret, raw_body),
            "X-Webhook-Id": str(d.id),
            "X-Webhook-Timestamp": datetime.utcnow().isoformat(),
        }
        max_retries = d.max_retries or 5

        try:
            resp = requests.post(url, data=raw_body, headers=headers,
                                 timeout=DELIVERY_TIMEOUT_SEC)
            status_code = resp.status_code
            snippet = (resp.text or "")[:500]
        except Exception as exc:
            status_code = None
            snippet = f"{type(exc).__name__}: {exc}"[:500]

        d.http_status = status_code
        d.response_snippet = snippet
        if status_code is not None and 200 <= status_code < 300:
            d.status = "success"
            d.finished_at = datetime.utcnow()
            return
        d.retries = (d.retries or 0) + 1
        if d.retries >= max_retries:
            d.status = "failed"
            d.finished_at = datetime.utcnow()
        else:
            d.next_retry_at = datetime.utcnow() + _backoff(d.retries)
```

> `_deliver_one` 用独立 `session_scope`,与 spine_queue 的乐观锁/心跳同构。`d.status != "pending"` 守卫保证扫描重入不重复处理(幂等)。

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && python -m pytest tests/test_webhooks.py -k dispatch_pending -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add backend/app/webhooks.py backend/tests/test_webhooks.py
git commit -m "feat(webhook): dispatch_pending 扫描投递+签名+退避重试

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: 接入 spine 触发点

**Files:**
- Modify: `backend/app/spine_queue.py`(`execute_job` 成功分支 `:125`、`_handle_failure` failed 分支 `:160`)
- Test: `backend/tests/test_webhooks.py`

- [ ] **Step 1: 写失败测试 — spine 成功后登记 delivery**

```python
def test_spine_success_enqueues_delivery():
    """spine execute_job 成功后,若该 workspace 有 active 配置,应登记一条 delivery。"""
    from unittest.mock import patch
    from app.db import SessionLocal
    from app.models import SpineJob, WebhookDelivery
    from app.spine_queue import claim_job, enqueue, execute_job
    init_db()
    ws = 6001
    _make_active_config(ws)
    # 清场 + 入队
    s = SessionLocal()
    s.query(SpineJob).filter(SpineJob.status == "pending").delete()
    s.commit()
    jid = enqueue(s, "https://x.com/p/1", "wh-set", workspace_id=ws)
    s.commit(); s.close()
    assert claim_job("wh-test-worker") == jid

    def _scrape_stub(db, url, **kw):
        return {"scrape_id": "s", "url": url,
                "data": {"title": "M", "confidence": 0.9},
                "metadata": {}, "html": "<html/>", "warnings": [],
                "usage": {"source": "live", "credits_used": 2}}

    with patch("app.spine._do_scrape", side_effect=_scrape_stub):
        execute_job(jid)
    s = SessionLocal()
    try:
        rows = s.query(WebhookDelivery).filter(
            WebhookDelivery.job_kind == "spine",
            WebhookDelivery.job_id == jid).all()
        assert len(rows) == 1
        assert rows[0].event_type == "job.success"
        assert rows[0].payload["job"]["result"].get("record_id") is not None
    finally:
        s.close()
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && python -m pytest tests/test_webhooks.py::test_spine_success_enqueues_delivery -v`
Expected: FAIL — 查不到 delivery(触发点未接)

- [ ] **Step 3: 在 spine_queue.py 接入**

成功分支(`execute_job` 内,当前 `:125-130`)。把:

```python
                job.status = "success"
                job.result_record_id = out.get("record_id")
                job.finished_at = datetime.utcnow()
                job.error = None
                return {"job_id": job_id, "status": "success",
                        "record_id": out.get("record_id")}
```

改为(在 return 前插登记):

```python
                job.status = "success"
                job.result_record_id = out.get("record_id")
                job.finished_at = datetime.utcnow()
                job.error = None
                from .webhooks import enqueue_delivery
                enqueue_delivery(
                    s, workspace_id=workspace_id, job_kind="spine",
                    job_id=job_id, status="success", error=None,
                    created_at=job.created_at, finished_at=job.finished_at,
                    result={"record_id": out.get("record_id"),
                            "url": url, "dataset": dataset_name})
                return {"job_id": job_id, "status": "success",
                        "record_id": out.get("record_id")}
```

失败分支(`_handle_failure`,当前 `:160-162` 的 failed 终态)。把:

```python
    job.status = "failed"
    job.finished_at = datetime.utcnow()
    return {"job_id": job.id, "status": "failed", "retries": job.retries}
```

改为:

```python
    job.status = "failed"
    job.finished_at = datetime.utcnow()
    from .webhooks import enqueue_delivery
    enqueue_delivery(
        s, workspace_id=job.workspace_id, job_kind="spine", job_id=job.id,
        status="failed", error=str(exc), created_at=job.created_at,
        finished_at=job.finished_at,
        result={"url": job.url, "dataset": job.dataset})
    return {"job_id": job.id, "status": "failed", "retries": job.retries}
```

> 注:`_handle_failure` 同时被「中途重试(回 pending)」和「超限(failed)」两条路径调用。
> enqueue_delivery 只加在 **failed 终态分支**(上面这段),pending 重试分支不动——符合「仅终态」。

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && python -m pytest tests/test_webhooks.py::test_spine_success_enqueues_delivery -v`
Expected: PASS

回归:`cd backend && python -m pytest tests/test_spine_queue.py tests/test_spine_queue_api.py -q`
Expected: 全 PASS(接入是事务内追加,不改原状态流转)

- [ ] **Step 5: 提交**

```bash
git add backend/app/spine_queue.py backend/tests/test_webhooks.py
git commit -m "feat(webhook): spine execute_job 终态登记 delivery

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: 接入 crawl 触发点

**Files:**
- Modify: `backend/app/runner.py`(success `:111`、failed `:93`、blocked `:83`)
- Test: `backend/tests/test_webhooks.py`

- [ ] **Step 1: 写失败测试 — crawl 成功/失败/熔断都登记**

```python
def test_crawl_terminal_enqueues_delivery():
    """crawl execute_job 三种终态(success/failed/blocked)都登记 delivery。"""
    from unittest.mock import patch
    from app.db import SessionLocal
    from app.models import CrawlJob, Site, WebhookDelivery
    from app import runner
    init_db()
    ws = 6101
    _make_active_config(ws)
    s = SessionLocal()
    # 确保有一个站点可入队
    site = s.query(Site).first()
    assert site is not None, "需要至少一个 seed 站点"
    site_name = site.site
    job = CrawlJob(site=site_name, status="pending", trigger="test",
                   requested_by_workspace_id=ws)
    s.add(job); s.commit()
    jid = job.id
    s.close()

    # mock crawler.crawl 抛异常 → failed 路径
    class _BoomCrawler:
        def crawl(self):
            raise RuntimeError("mock 抓取失败")

    with patch("app.runner.get_crawler", return_value=_BoomCrawler()), \
         patch("app.runner.in_cooldown", return_value=False):
        runner.execute_job(jid)

    s = SessionLocal()
    try:
        rows = s.query(WebhookDelivery).filter(
            WebhookDelivery.job_kind == "crawl",
            WebhookDelivery.job_id == jid).all()
        assert len(rows) == 1
        assert rows[0].event_type == "job.failed"
        assert "mock 抓取失败" in (rows[0].payload["job"]["error"] or "")
    finally:
        s.close()
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && python -m pytest tests/test_webhooks.py::test_crawl_terminal_enqueues_delivery -v`
Expected: FAIL — 查不到 delivery

- [ ] **Step 3: 在 runner.py 三个终态接入**

每个终态的 `with session_scope() as s:` 块内、设完 job 状态后、return 前插一行登记。`requested_by_workspace_id` 在 job 上。

**failed 分支**(`:89-100`),在 `_fsite` 处理后、return 前加:

```python
            from .webhooks import enqueue_delivery
            enqueue_delivery(
                s, workspace_id=job.requested_by_workspace_id,
                job_kind="crawl", job_id=job_id, status="failed",
                error=str(exc), created_at=job.created_at,
                finished_at=job.finished_at, result={"site": site_name})
```

**blocked 分支**(`:79-88`),在设完 blocked、return 前加(blocked 归 failed):

```python
            from .webhooks import enqueue_delivery
            enqueue_delivery(
                s, workspace_id=job.requested_by_workspace_id,
                job_kind="crawl", job_id=job_id, status="failed",
                error=f"熔断:{exc}(站点已进入冷却期)",
                created_at=job.created_at, finished_at=job.finished_at,
                result={"site": site_name})
```

**success 分支**(`:102-126` 的 `with session_scope()` 块内),在设完所有统计字段后、`return {...}` 前加:

```python
        from .webhooks import enqueue_delivery
        enqueue_delivery(
            s, workspace_id=job.requested_by_workspace_id,
            job_kind="crawl", job_id=job_id, status="success", error=None,
            created_at=job.created_at, finished_at=job.finished_at,
            result={"site": site_name,
                    "products_count": job.products_count,
                    "new_count": job.new_count,
                    "promotion_count": job.promotion_count})
```

> skipped 分支(站点冷却跳过,`:68-74`)不登记——它不是真正的任务结果终态,spec「仅终态 success/failed」不含 skipped。

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && python -m pytest tests/test_webhooks.py::test_crawl_terminal_enqueues_delivery -v`
Expected: PASS

回归:`cd backend && python -m pytest tests/test_routes_smoke.py tests/test_tracking_api.py -q`
Expected: 全 PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/runner.py backend/tests/test_webhooks.py
git commit -m "feat(webhook): crawl execute_job 三终态登记 delivery(blocked 归 failed)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: 接入 ondemand 触发点

**Files:**
- Modify: `backend/app/ondemand/queue.py`(`process_one` 终态写回块 `:95-105`)
- Test: `backend/tests/test_webhooks.py`

- [ ] **Step 1: 写失败测试 — ondemand 终态登记**

```python
def test_ondemand_terminal_enqueues_delivery():
    from unittest.mock import patch
    from app.db import SessionLocal
    from app.models import OnDemandJob, WebhookDelivery
    from app.ondemand import queue as odq
    init_db()
    ws = 6201
    _make_active_config(ws)
    s = SessionLocal()
    job = OnDemandJob(url="https://x.com/p/1", status="queued",
                      platform="generic", workspace_id=ws,
                      max_items=10, review_limit=10)
    s.add(job); s.commit()
    jid = job.id
    s.close()

    class _Res:
        listings = [{"sku": "A1"}]
        reviews = []
        notes = []

    with patch("app.ondemand.runner.fetch", return_value=_Res()):
        odq.process_one(jid)

    s = SessionLocal()
    try:
        rows = s.query(WebhookDelivery).filter(
            WebhookDelivery.job_kind == "ondemand",
            WebhookDelivery.job_id == jid).all()
        assert len(rows) == 1
        assert rows[0].event_type == "job.success"
        assert rows[0].payload["job"]["result"]["listing_count"] == 1
        assert rows[0].payload["job"]["result"]["item_skus"] == ["A1"]
    finally:
        s.close()
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && python -m pytest tests/test_webhooks.py::test_ondemand_terminal_enqueues_delivery -v`
Expected: FAIL — 查不到 delivery

- [ ] **Step 3: 在 ondemand/queue.py 接入**

`process_one` 末尾终态写回块(当前 `:95-106`)。把:

```python
    with session_scope() as s:
        job = s.get(OnDemandJob, job_id)
        if job is None:
            return
        job.status = status
        job.finished_at = datetime.utcnow()
        job.listing_count = len(listings)
        job.review_count = len(reviews)
        job.notes = notes
        job.item_skus = skus
        job.error = error
```

改为(末尾加登记):

```python
    with session_scope() as s:
        job = s.get(OnDemandJob, job_id)
        if job is None:
            return
        job.status = status
        job.finished_at = datetime.utcnow()
        job.listing_count = len(listings)
        job.review_count = len(reviews)
        job.notes = notes
        job.item_skus = skus
        job.error = error
        from ..webhooks import enqueue_delivery
        enqueue_delivery(
            s, workspace_id=job.workspace_id, job_kind="ondemand",
            job_id=job_id, status="success" if status == "success" else "failed",
            error=error, created_at=job.created_at,
            finished_at=job.finished_at,
            result={"listing_count": len(listings),
                    "review_count": len(reviews),
                    "batch_id": job.batch_id, "item_skus": skus})
```

> ondemand 的 `partial` 也算 success 类(有数据);只有 `failed` 映射 job.failed。status=="success" 判定保持与 spec 一致:partial→job.failed?否——partial 是「有数据但带 notes」,属成功侧。修正:`status="success" if status in ("success", "partial") else "failed"`。

实际写 Step 3 时用:

```python
            status="success" if status in ("success", "partial") else "failed",
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && python -m pytest tests/test_webhooks.py::test_ondemand_terminal_enqueues_delivery -v`
Expected: PASS

回归:`cd backend && python -m pytest tests/test_ondemand_queue.py tests/test_ondemand_jobs.py -q`
Expected: 全 PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/ondemand/queue.py backend/tests/test_webhooks.py
git commit -m "feat(webhook): ondemand process_one 终态登记 delivery(partial 归 success)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: spine_worker 每轮 dispatch_pending

**Files:**
- Modify: `backend/app/spine_worker.py`(`run_loop` 内)
- Test: `backend/tests/test_webhooks.py`

- [ ] **Step 1: 写失败测试 — run_loop 一轮会调 dispatch_pending**

```python
def test_spine_worker_loop_calls_dispatch():
    """run_loop 每轮应调 dispatch_pending(投递扫描挂在 spine_worker)。"""
    from unittest.mock import patch
    from app import spine_worker
    init_db()
    calls = {"n": 0}

    def _count_dispatch():
        calls["n"] += 1
        return 0

    # should_continue 第一次 True、之后 False:只跑一轮
    seq = iter([True, False])

    def _cont():
        return next(seq, False)

    with patch("app.spine_worker.dispatch_pending", side_effect=_count_dispatch), \
         patch("app.spine_worker.claim_job", return_value=None), \
         patch("app.spine_worker.reclaim_stale_jobs", return_value=0), \
         patch("app.spine_worker.time.sleep", return_value=None):
        spine_worker.run_loop(poll_interval=0, should_continue=_cont)
    assert calls["n"] >= 1
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && python -m pytest tests/test_webhooks.py::test_spine_worker_loop_calls_dispatch -v`
Expected: FAIL — `dispatch_pending` 未在 spine_worker 中(AttributeError on patch)

- [ ] **Step 3: 在 spine_worker.py 接入**

顶部 import(`:16` 附近)加:

```python
from .webhooks import dispatch_pending
```

`run_loop` 循环体内(当前 `reclaim_stale_jobs` 之后、`claim_job` 之前)加一段:

```python
        try:
            sent = dispatch_pending()
            if sent:
                logger.info("dispatch %d 条 webhook", sent)
        except Exception as exc:
            logger.error("dispatch_pending 异常: %s", exc)
```

> 放在 reclaim 之后、claim 之前:每轮先回收悬挂 job、再发 webhook、再领新活。dispatch_pending 自身已全程容错,这里再包一层防御。

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && python -m pytest tests/test_webhooks.py::test_spine_worker_loop_calls_dispatch -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/spine_worker.py backend/tests/test_webhooks.py
git commit -m "feat(webhook): spine_worker 每轮扫描投递 dispatch_pending

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: 配置 API — GET/PUT/DELETE /webhook

**Files:**
- Create: `backend/app/api/webhook.py`
- Modify: `backend/app/main.py`(注册 router)
- Test: `backend/tests/test_webhooks.py`

- [ ] **Step 1: 写失败测试 — PUT 建配置 + GET 掩码 + 租户隔离**

```python
def _make_api_key(scopes, workspace_id):
    """造一个带 workspace 的 api_key,返回 raw key。"""
    from app.apikey import generate, hash_key, short
    from app.db import SessionLocal
    from app.models import ApiKey
    raw = generate()
    s = SessionLocal()
    try:
        s.add(ApiKey(name="wh", key_prefix=short(raw), key_hash=hash_key(raw),
                     scopes=scopes, active=True, workspace_id=workspace_id))
        s.commit()
    finally:
        s.close()
    return raw


def test_webhook_put_then_get_masks_secret():
    from fastapi.testclient import TestClient
    from app.main import app
    init_db()
    raw = _make_api_key(["crawler:scrape", "crawler:read"], workspace_id=7001)
    client = TestClient(app)
    h = {"X-API-Key": raw}
    # PUT 建配置 —— 响应里返回一次明文 secret
    r = client.put("/api/v2/webhook", headers=h,
                   json={"url": "https://hooks.example.com/ingest"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["url"] == "https://hooks.example.com/ingest"
    assert body["active"] is True
    assert body["secret"].startswith("whsec_")    # 明文只此一次
    # GET —— secret 掩码
    g = client.get("/api/v2/webhook", headers=h)
    assert g.status_code == 200
    assert "•" in g.json()["secret"] or g.json()["secret"].endswith(
        body["secret"][-4:])
    assert g.json()["secret"] != body["secret"]    # 不再回明文


def test_webhook_put_rejects_private_url():
    from fastapi.testclient import TestClient
    from app.main import app
    init_db()
    raw = _make_api_key(["crawler:scrape"], workspace_id=7002)
    client = TestClient(app)
    r = client.put("/api/v2/webhook", headers={"X-API-Key": raw},
                   json={"url": "http://127.0.0.1/hook"})
    assert r.status_code == 400, r.text


def test_webhook_delete_stops_notifications():
    from fastapi.testclient import TestClient
    from app.main import app
    init_db()
    raw = _make_api_key(["crawler:scrape", "crawler:read"], workspace_id=7003)
    client = TestClient(app)
    h = {"X-API-Key": raw}
    client.put("/api/v2/webhook", headers=h,
               json={"url": "https://hooks.example.com/x"})
    d = client.delete("/api/v2/webhook", headers=h)
    assert d.status_code == 200
    g = client.get("/api/v2/webhook", headers=h)
    assert g.status_code == 404    # 删后查不到


def test_webhook_requires_auth():
    from fastapi.testclient import TestClient
    from app.main import app
    init_db()
    client = TestClient(app)
    r = client.get("/api/v2/webhook")
    assert r.status_code in (401, 403)
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && python -m pytest tests/test_webhooks.py -k "webhook_put or webhook_delete or webhook_requires" -v`
Expected: FAIL — 路由不存在(404 而非预期)

- [ ] **Step 3: 创建 api/webhook.py + 注册**

`backend/app/api/webhook.py`:

```python
"""租户 webhook 配置 REST 端点。挂在 /api/v2/webhook 下。

复用 v2.py 的 api_key 鉴权 + workspace 解析:租户只能读写自己的配置。
secret 明文仅在创建/轮换响应返回一次,其余只给掩码。
"""
from __future__ import annotations

import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..access import find_api_key, raw_key_from_headers, require_api_key_scope
from ..db import get_db
from ..models import ApiKey, WebhookConfig, WebhookDelivery
from ..webhooks import WebhookUrlError, validate_webhook_url

router = APIRouter(prefix="/api/v2/webhook", tags=["webhook"])


def _key_row(db: Session, authorization: str, x_api_key: str) -> ApiKey | None:
    raw = raw_key_from_headers(authorization, x_api_key)
    return find_api_key(db, raw) if raw else None


def _require_ws(db: Session, authorization: str, x_api_key: str) -> int:
    key = _key_row(db, authorization, x_api_key)
    require_api_key_scope(key, "crawler:scrape")
    if not key or key.workspace_id is None:
        raise HTTPException(403, "api_key 未关联 workspace")
    return key.workspace_id


def _mask(secret: str) -> str:
    return f"whsec_••••{secret[-4:]}" if secret else ""


def _gen_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(24)


class WebhookPutRequest(BaseModel):
    url: str
    active: bool = True


@router.get("")
def get_webhook(authorization: str = Header(default=""),
                x_api_key: str = Header(default="", alias="X-API-Key"),
                db: Session = Depends(get_db)):
    ws = _require_ws(db, authorization, x_api_key)
    cfg = db.query(WebhookConfig).filter(
        WebhookConfig.workspace_id == ws).first()
    if cfg is None:
        raise HTTPException(404, "未配置 webhook")
    return {"url": cfg.url, "active": cfg.active,
            "secret": _mask(cfg.secret),
            "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None}


@router.put("")
def put_webhook(req: WebhookPutRequest,
                authorization: str = Header(default=""),
                x_api_key: str = Header(default="", alias="X-API-Key"),
                db: Session = Depends(get_db)):
    ws = _require_ws(db, authorization, x_api_key)
    try:
        validate_webhook_url(req.url)
    except WebhookUrlError as exc:
        raise HTTPException(400, str(exc))
    cfg = db.query(WebhookConfig).filter(
        WebhookConfig.workspace_id == ws).first()
    plaintext_secret = None
    if cfg is None:
        plaintext_secret = _gen_secret()
        cfg = WebhookConfig(workspace_id=ws, url=req.url,
                            secret=plaintext_secret, active=req.active,
                            created_at=datetime.utcnow(),
                            updated_at=datetime.utcnow())
        db.add(cfg)
    else:
        cfg.url = req.url
        cfg.active = req.active
        cfg.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(cfg)
    # 仅新建时回明文 secret;更新沿用既有(掩码)
    return {"url": cfg.url, "active": cfg.active,
            "secret": plaintext_secret if plaintext_secret else _mask(cfg.secret)}


@router.delete("")
def delete_webhook(authorization: str = Header(default=""),
                   x_api_key: str = Header(default="", alias="X-API-Key"),
                   db: Session = Depends(get_db)):
    ws = _require_ws(db, authorization, x_api_key)
    cfg = db.query(WebhookConfig).filter(
        WebhookConfig.workspace_id == ws).first()
    if cfg is not None:
        db.delete(cfg)
        db.commit()
    return {"deleted": True}
```

注册到 `backend/app/main.py`:找到现有 `app.include_router(...)` 群(v2 router 注册处),追加:

```python
from .api.webhook import router as webhook_router
app.include_router(webhook_router)
```

> 注:先 grep `grep -n "include_router" backend/app/main.py` 定位现有注册风格,与之并列即可。

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && python -m pytest tests/test_webhooks.py -k "webhook_put or webhook_delete or webhook_requires" -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add backend/app/api/webhook.py backend/app/main.py backend/tests/test_webhooks.py
git commit -m "feat(webhook): 配置 REST 端点 GET/PUT/DELETE(secret 掩码+SSRF校验)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 13: 运维 API — rotate-secret / deliveries / test

**Files:**
- Modify: `backend/app/api/webhook.py`
- Test: `backend/tests/test_webhooks.py`

- [ ] **Step 1: 写失败测试 — 轮换/列表/测试**

```python
def test_webhook_rotate_secret_changes_value():
    from fastapi.testclient import TestClient
    from app.main import app
    init_db()
    raw = _make_api_key(["crawler:scrape", "crawler:read"], workspace_id=7101)
    client = TestClient(app)
    h = {"X-API-Key": raw}
    r1 = client.put("/api/v2/webhook", headers=h,
                    json={"url": "https://hooks.example.com/x"})
    old = r1.json()["secret"]
    r2 = client.post("/api/v2/webhook/rotate-secret", headers=h)
    assert r2.status_code == 200, r2.text
    new = r2.json()["secret"]
    assert new.startswith("whsec_") and new != old


def test_webhook_deliveries_lists_recent():
    from fastapi.testclient import TestClient
    from app.db import SessionLocal
    from app.main import app
    from app.models import WebhookConfig, WebhookDelivery
    init_db()
    ws = 7102
    raw = _make_api_key(["crawler:scrape", "crawler:read"], workspace_id=ws)
    client = TestClient(app)
    h = {"X-API-Key": raw}
    client.put("/api/v2/webhook", headers=h,
               json={"url": "https://hooks.example.com/x"})
    # 插一条投递记录
    s = SessionLocal()
    cfg = s.query(WebhookConfig).filter(WebhookConfig.workspace_id == ws).first()
    s.add(WebhookDelivery(workspace_id=ws, config_id=cfg.id,
                          event_type="job.success", job_kind="spine",
                          job_id=1, status="success",
                          payload={}, http_status=200,
                          created_at=datetime.utcnow()))
    s.commit(); s.close()
    g = client.get("/api/v2/webhook/deliveries", headers=h)
    assert g.status_code == 200
    items = g.json()["items"]
    assert len(items) >= 1
    assert items[0]["status"] == "success"
    assert items[0]["http_status"] == 200


def test_webhook_test_sends_sample():
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    from app.main import app
    init_db()
    raw = _make_api_key(["crawler:scrape"], workspace_id=7103)
    client = TestClient(app)
    h = {"X-API-Key": raw}
    client.put("/api/v2/webhook", headers=h,
               json={"url": "https://hooks.example.com/x"})

    class _R:
        status_code = 200
        text = "ok"

    with patch("app.webhooks.requests.post", return_value=_R()):
        r = client.post("/api/v2/webhook/test", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["http_status"] == 200
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && python -m pytest tests/test_webhooks.py -k "rotate or deliveries or webhook_test" -v`
Expected: FAIL — 路由不存在

- [ ] **Step 3: 在 api/webhook.py 追加三端点**

顶部 import 补:`from ..webhooks import send_test_delivery`(下一步在 webhooks.py 实现)。追加:

```python
@router.post("/rotate-secret")
def rotate_secret(authorization: str = Header(default=""),
                  x_api_key: str = Header(default="", alias="X-API-Key"),
                  db: Session = Depends(get_db)):
    ws = _require_ws(db, authorization, x_api_key)
    cfg = db.query(WebhookConfig).filter(
        WebhookConfig.workspace_id == ws).first()
    if cfg is None:
        raise HTTPException(404, "未配置 webhook")
    new_secret = _gen_secret()
    cfg.secret = new_secret
    cfg.updated_at = datetime.utcnow()
    db.commit()
    return {"secret": new_secret}   # 明文回一次


@router.get("/deliveries")
def list_deliveries(limit: int = 20,
                    authorization: str = Header(default=""),
                    x_api_key: str = Header(default="", alias="X-API-Key"),
                    db: Session = Depends(get_db)):
    ws = _require_ws(db, authorization, x_api_key)
    limit = max(1, min(limit, 100))
    rows = (db.query(WebhookDelivery)
            .filter(WebhookDelivery.workspace_id == ws)
            .order_by(WebhookDelivery.id.desc())
            .limit(limit).all())
    return {"items": [{
        "id": d.id, "event_type": d.event_type, "job_kind": d.job_kind,
        "job_id": d.job_id, "status": d.status, "retries": d.retries,
        "http_status": d.http_status,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "finished_at": d.finished_at.isoformat() if d.finished_at else None,
    } for d in rows]}


@router.post("/test")
def test_webhook(authorization: str = Header(default=""),
                 x_api_key: str = Header(default="", alias="X-API-Key"),
                 db: Session = Depends(get_db)):
    ws = _require_ws(db, authorization, x_api_key)
    cfg = db.query(WebhookConfig).filter(
        WebhookConfig.workspace_id == ws).first()
    if cfg is None:
        raise HTTPException(404, "未配置 webhook")
    return send_test_delivery(cfg.url, cfg.secret)
```

在 `backend/app/webhooks.py` 追加 `send_test_delivery`(同步发一条样例,立即返回结果,不入投递表):

```python
def send_test_delivery(url: str, secret: str) -> dict:
    """同步发一条测试 payload,立即返回 {http_status, ok, error}。不入投递表。"""
    payload = {
        "event": "webhook.test",
        "webhook_id": "whd_test",
        "timestamp": datetime.utcnow().isoformat(),
        "job": {"id": 0, "kind": "test", "status": "success",
                "result": {"message": "这是一条 smart-crawler webhook 测试通知"}},
    }
    raw_body = json.dumps(payload, ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Signature": sign_payload(secret, raw_body),
        "X-Webhook-Id": "test",
        "X-Webhook-Timestamp": datetime.utcnow().isoformat(),
    }
    try:
        resp = requests.post(url, data=raw_body, headers=headers,
                             timeout=DELIVERY_TIMEOUT_SEC)
        return {"http_status": resp.status_code,
                "ok": 200 <= resp.status_code < 300,
                "error": None}
    except Exception as exc:
        return {"http_status": None, "ok": False,
                "error": f"{type(exc).__name__}: {exc}"}
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && python -m pytest tests/test_webhooks.py -k "rotate or deliveries or webhook_test" -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add backend/app/api/webhook.py backend/app/webhooks.py backend/tests/test_webhooks.py
git commit -m "feat(webhook): rotate-secret / deliveries / test 运维端点

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 14: 控制台 UI — Webhook 配置卡片

**Files:**
- Modify: `frontend/<console index.html>`(实际路径用 grep 定位)
- 无独立单测(前端纯静态);手动验收

- [ ] **Step 1: 定位 console 文件与设置区**

Run:
```bash
grep -rln "X-API-Key\|api/v2\|设置\|Settings" frontend/ | head
```
找到承载租户控制台设置区的 index.html。确认它是 memory 记录的「深色基础 + 浅色 override 两层」结构——改配色须同时动 `--ui-*` 变量 + 字面量 + 内联补丁(见 [[console-light-theme-override]])。

- [ ] **Step 2: 加 Webhook 卡片 HTML**

在设置区追加一个卡片(沿用页面现有卡片 class/结构,以下为结构示意,实际 class 名对齐邻近卡片):

```html
<section class="card" id="webhook-card">
  <h3>Webhook 通知</h3>
  <p class="muted">任务完成(成功/失败)时,向你的地址 POST 一条带签名的通知。</p>
  <label>目标地址
    <input id="wh-url" type="url" placeholder="https://your.app/webhooks/smart-crawler">
  </label>
  <label><input id="wh-active" type="checkbox" checked> 启用</label>
  <div class="row">
    <span>签名密钥:</span><code id="wh-secret">—</code>
    <button id="wh-rotate">轮换</button>
  </div>
  <div class="row">
    <button id="wh-save">保存</button>
    <button id="wh-test">发送测试</button>
    <span id="wh-status" class="muted"></span>
  </div>
  <h4>最近投递</h4>
  <ul id="wh-deliveries" class="muted"></ul>
</section>
```

- [ ] **Step 3: 加 JS 逻辑(对接 6 端点)**

沿用页面现有的 fetch helper(带 X-API-Key 的封装);若无则用原生 fetch + 现有取 key 方式。脚本要点:

```javascript
const WH = {
  async load() {
    const r = await fetch('/api/v2/webhook', {headers: authHeaders()});
    if (r.status === 404) { document.getElementById('wh-secret').textContent = '(未配置)'; return; }
    const d = await r.json();
    document.getElementById('wh-url').value = d.url || '';
    document.getElementById('wh-active').checked = !!d.active;
    document.getElementById('wh-secret').textContent = d.secret || '—';
    WH.loadDeliveries();
  },
  async save() {
    const url = document.getElementById('wh-url').value;
    const active = document.getElementById('wh-active').checked;
    const r = await fetch('/api/v2/webhook', {
      method: 'PUT', headers: {...authHeaders(), 'Content-Type': 'application/json'},
      body: JSON.stringify({url, active})});
    const d = await r.json();
    const st = document.getElementById('wh-status');
    if (!r.ok) { st.textContent = '保存失败:' + (d.detail || r.status); return; }
    st.textContent = '已保存';
    if (d.secret && d.secret.startsWith('whsec_') && !d.secret.includes('•')) {
      document.getElementById('wh-secret').textContent = d.secret;
      alert('请记录签名密钥(只显示这一次):\n' + d.secret);
    }
    WH.loadDeliveries();
  },
  async rotate() {
    if (!confirm('轮换后旧签名立即失效,确认?')) return;
    const r = await fetch('/api/v2/webhook/rotate-secret', {method: 'POST', headers: authHeaders()});
    const d = await r.json();
    if (r.ok) { document.getElementById('wh-secret').textContent = d.secret;
                alert('新签名密钥(只显示这一次):\n' + d.secret); }
  },
  async test() {
    const st = document.getElementById('wh-status');
    st.textContent = '发送中…';
    const r = await fetch('/api/v2/webhook/test', {method: 'POST', headers: authHeaders()});
    const d = await r.json();
    st.textContent = d.ok ? '测试成功 (HTTP ' + d.http_status + ')'
                          : '测试失败:' + (d.error || ('HTTP ' + d.http_status));
  },
  async loadDeliveries() {
    const r = await fetch('/api/v2/webhook/deliveries?limit=10', {headers: authHeaders()});
    if (!r.ok) return;
    const ul = document.getElementById('wh-deliveries');
    ul.innerHTML = '';
    for (const d of r.json ? (await r.json()).items : []) {
      const li = document.createElement('li');
      const ok = d.status === 'success';
      li.textContent = `${ok ? '✓' : '✗'} ${d.job_kind}#${d.job_id} ${d.event_type} ` +
                       `HTTP ${d.http_status ?? '-'} ${d.created_at || ''}`;
      li.style.color = ok ? 'var(--ok, green)' : 'var(--err, #c33)';
      ul.appendChild(li);
    }
  },
};
document.getElementById('wh-save').onclick = WH.save;
document.getElementById('wh-rotate').onclick = WH.rotate;
document.getElementById('wh-test').onclick = WH.test;
WH.load();
```

> `authHeaders()` 用页面已有的取 key 方式(grep `X-API-Key` 看现有调用怎么带 key),不要新造一套。

- [ ] **Step 4: 手动验收**

Run(本地起服务): 用 `/run` 或现有启动方式拉起后端 + 打开 console,逐项点:填 URL 保存→看 secret 弹出一次→刷新看掩码→发送测试(可临时用 https://webhook.site 接收)→看投递列表出现一条 ✓/✗。

- [ ] **Step 5: 提交**

```bash
git add frontend/
git commit -m "feat(webhook): 控制台 Webhook 配置卡片(URL/密钥/测试/投递列表)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 15: 全量回归 + 文档

**Files:**
- 无新代码;跑全测 + 更新 spec 状态

- [ ] **Step 1: 跑 webhook 全测**

Run: `cd backend && python -m pytest tests/test_webhooks.py -v`
Expected: 全 PASS

- [ ] **Step 2: 跑相关回归**

Run: `cd backend && python -m pytest tests/test_spine_queue.py tests/test_spine_queue_api.py tests/test_ondemand_queue.py tests/test_ondemand_jobs.py tests/test_routes_smoke.py tests/test_workspace_tenancy.py -q`
Expected: 全 PASS(确认接入未破坏既有队列/租户行为)

- [ ] **Step 3: 更新 spec 状态**

把 spec 文件 `docs/superpowers/specs/2026-06-12-tenant-webhook-notifications-design.md` 头部 `> **状态:**` 改为 `已实现,待部署`。

- [ ] **Step 4: 提交**

```bash
git add docs/superpowers/specs/2026-06-12-tenant-webhook-notifications-design.md
git commit -m "docs(webhook): spec 标记已实现

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 5: 部署说明(留给操作者)**

部署走 `smart-crawler-nas-deploy` skill。关键:**确保生产 spine_worker 常驻运行**——投递扫描挂在它的 run_loop;若它停摆,delivery 会积压在表里等其恢复后补发(不丢,有延迟)。SSRF 默认拦内网段:若租户 webhook 目标在 Tailscale/内网,需在 `validate_webhook_url` 加白名单豁免(当前未做,见 spec 非目标)。

---

## 自检结果(写计划后回查 spec)

**1. spec 覆盖**
- 数据模型(spec §1)→ Task 1 ✓
- 投递流程/enqueue_delivery/dispatch_pending(§2)→ Task 5/7/11 ✓
- 三触发点(§2 表)→ Task 8/9/10 ✓
- 瘦载荷(§3)→ Task 2 ✓
- 配置 API 6 端点(§4)→ Task 12/13 ✓
- 控制台 UI(§4)→ Task 14 ✓
- HMAC 签名(§5)→ Task 3 ✓
- SSRF 校验(§5)→ Task 4 ✓
- 失败上限/退避(§5)→ Task 6/7 ✓
- 测试策略(§6)→ 各 Task 内 TDD ✓

**2. placeholder 扫描**:无 TBD/TODO/「implement later」。每个代码步骤都给了完整可运行代码。Task 8 的失败终态接入明确只加在 `_handle_failure` 的 failed 分支(非 pending 重试分支)。

**3. 类型/签名一致性**:`enqueue_delivery` 关键字参数(workspace_id/job_kind/job_id/status/error/created_at/finished_at/result)在 Task 5 定义,Task 8/9/10 调用一致 ✓;`build_payload`/`sign_payload`/`dispatch_pending`/`_backoff`/`validate_webhook_url`/`send_test_delivery` 跨 Task 命名一致 ✓;`WebhookConfig`/`WebhookDelivery` 列名在 Task 1 定义,后续引用一致 ✓。

**4. 歧义**:ondemand 的 partial 归属在 Task 10 明确为 success 侧(`status in ("success","partial")`);crawl 的 blocked 在 Task 9 明确归 job.failed;spine failed 仅超重试上限触发(Task 8 改 `_handle_failure` 的 failed 终态分支,非中途 pending)。
