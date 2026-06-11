# 标杆网站维护面板 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 smart-crawler 控制台新增"标杆网站维护"面板：admin 贴 URL 自动探测平台(Shopify/sitemap)、建站并触发一次抓取，可增删改/暂停追踪，复用现有 crawler/Trend/report 全链路。

**Architecture:** 扩展现有 `Site` 表加 6 列（迁移走 `db.py::_migrate()` 幂等 ADD COLUMN）；新建 `crawlers/detect.py` 探测平台；新增一组 `/api/tracking*` 端点（admin 门控、workspace 作用域）；新建 Vue `TrackingPage.vue` + `api/tracking.ts` 注册为新 tab。products/30天销量/收入实时从 Product 表算，不冗余存储。

**Tech Stack:** FastAPI + SQLAlchemy（SQLite 本地 / PG 生产）、curl_cffi（探测）、Vue 3 + Vite + Pinia、pytest、vue-tsc。

**分支:** `feat/benchmark-tracking-panel`（已建，spec 已 commit 在此分支）。

**前置:** 本会话 A/B 档代码改动尚未提交，仍在工作区。本计划的 commit 只 add 本功能涉及的文件，不裹挟 A/B 档改动。

**Spec:** `docs/superpowers/specs/2026-06-11-benchmark-tracking-panel-design.md`

---

## 文件结构

| 文件 | 职责 | 新建/改 |
|---|---|---|
| `backend/app/models.py` | `Site` 加 6 列 | 改 |
| `backend/app/crawlers/detect.py` | `detect_platform(url)` 平台探测 | 新建 |
| `backend/app/api/tracking.py` | `/api/tracking*` 路由 + tracking_row 序列化 | 新建 |
| `backend/app/main.py` | 注册 tracking_router | 改 |
| `backend/app/scheduler.py` | `_product_job` 加 paused 跳过 | 改 |
| `backend/app/runner.py` | 抓取收尾置 error/复位 track_status | 改 |
| `backend/tests/test_detect_platform.py` | 探测单测 | 新建 |
| `backend/tests/test_tracking_api.py` | CRUD/权限单测 | 新建 |
| `frontend-app/src/api/tracking.ts` | tracking API client | 新建 |
| `frontend-app/src/pages/TrackingPage.vue` | 面板页 | 新建 |
| `frontend-app/src/app/router.ts` | 注册 `/app/tracking` 路由 | 改 |
| `frontend-app/src/components/layout/AppLayout.vue` | 导航加 tab | 改 |

---

## Task 1: Site 模型加 6 列 + 迁移验证

**Files:**
- Modify: `backend/app/models.py:35`（`class Site` 的 `last_crawled` 之后）
- Test: `backend/tests/test_tracking_api.py`（建文件，先放迁移断言）

- [ ] **Step 1: 加列**

在 `backend/app/models.py` 的 `class Site` 中，`last_crawled = Column(DateTime)`（第 35 行）之后插入：

```python
    # 标杆追踪面板字段（2026-06-11）
    track_status = Column(String, default="tracking")  # tracking / paused / error
    source = Column(String, default="yaml")            # yaml(种子) / user(面板建)
    creator = Column(String)                            # 创建人 username
    review_rate = Column(Float)                         # 留评率(Edit 可改，影响销量估算)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime)
```

确认 `models.py` 顶部已 import `datetime` 与 `Float`（Product 已用 Float，datetime 已用），无需补 import。

- [ ] **Step 2: 写迁移断言测试**

新建 `backend/tests/test_tracking_api.py`：

```python
"""标杆追踪面板 API + 迁移测试。"""
from sqlalchemy import inspect

from app.db import engine, init_db


def test_site_has_tracking_columns():
    init_db()
    cols = {c["name"] for c in inspect(engine).get_columns("sites")}
    for col in ("track_status", "source", "creator", "review_rate",
                "created_at", "updated_at"):
        assert col in cols, f"sites 缺列 {col}"
```

- [ ] **Step 3: 跑测试确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_tracking_api.py::test_site_has_tracking_columns -v`
Expected: PASS（`init_db()` 调 `_migrate()` 自动加列）

- [ ] **Step 4: 迁移演练（真实库副本，幂等 + 零丢失）**

Run:
```bash
cd backend
cp ../data/smart_crawler.db /tmp/track_rehearsal.db
DATABASE_URL="sqlite:////tmp/track_rehearsal.db" .venv/bin/python -c "
from app.db import init_db; init_db(); init_db()
import sqlite3; c=sqlite3.connect('/tmp/track_rehearsal.db')
cols={r[1] for r in c.execute('PRAGMA table_info(sites)')}
assert {'track_status','source','creator','review_rate','created_at','updated_at'} <= cols
print('cols OK, products rows:', c.execute('SELECT count(*) FROM products').fetchone()[0])
"
rm -f /tmp/track_rehearsal.db
```
Expected: `cols OK, products rows: <非0>`，无报错（幂等两次）

- [ ] **Step 5: Commit**

```bash
git add backend/app/models.py backend/tests/test_tracking_api.py
git commit -m "feat(tracking): add 6 tracking columns to Site model"
```

---

## Task 2: 平台探测 detect_platform

**Files:**
- Create: `backend/app/crawlers/detect.py`
- Test: `backend/tests/test_detect_platform.py`

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_detect_platform.py`：

```python
"""平台探测单测（mock 网络，不真实请求）。"""
from unittest.mock import patch

from app.crawlers.detect import detect_platform


class _Resp:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("not json")
        return self._json


def test_normalizes_base_to_scheme_host():
    with patch("app.crawlers.detect._get", return_value=_Resp(404)):
        _, base = detect_platform("https://shop.example.com/collections/all?page=2")
    assert base == "https://shop.example.com"


def test_detects_shopify_via_products_json():
    def fake_get(url, **kw):
        if url.endswith("/products.json?limit=1"):
            return _Resp(200, json_data={"products": [{"id": 1}]})
        return _Resp(404)
    with patch("app.crawlers.detect._get", side_effect=fake_get):
        platform, _ = detect_platform("https://shop.example.com")
    assert platform == "shopify"


def test_detects_generic_via_sitemap():
    def fake_get(url, **kw):
        if url.endswith("/products.json?limit=1"):
            return _Resp(404)
        if url.endswith("/sitemap.xml"):
            return _Resp(200, text="<urlset><url><loc>x</loc></url></urlset>")
        return _Resp(404)
    with patch("app.crawlers.detect._get", side_effect=fake_get):
        platform, _ = detect_platform("https://store.example.com")
    assert platform == "generic"


def test_returns_none_when_undetectable():
    with patch("app.crawlers.detect._get", return_value=_Resp(404)):
        platform, _ = detect_platform("https://static.example.com")
    assert platform is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_detect_platform.py -v`
Expected: FAIL（`No module named 'app.crawlers.detect'`）

- [ ] **Step 3: 写实现**

新建 `backend/app/crawlers/detect.py`：

```python
"""平台探测 —— 从任意 URL 推断该用哪个 crawler。

只覆盖 Shopify(/products.json) 与通用 sitemap 两类。其余平台(含 Magento)
需人工在 sites.yaml 配 platform。探测失败只返回 None,绝不抛异常。
"""
from __future__ import annotations

from urllib.parse import urlparse

_TIMEOUT = 8


def _get(url: str, **kw):
    """单独函数,便于测试 patch。失败抛异常由调用方兜。"""
    from curl_cffi import requests as cffi
    return cffi.get(url, timeout=_TIMEOUT, impersonate="chrome", **kw)


def _safe_get(url: str):
    try:
        return _get(url)
    except Exception:
        return None


def normalize_base(url: str) -> str:
    """取 scheme+host,去 path/query(验收:仅维护网址固定部分)。"""
    p = urlparse(url if "://" in url else f"https://{url}")
    scheme = p.scheme or "https"
    return f"{scheme}://{p.netloc}"


def detect_platform(url: str) -> tuple[str | None, str]:
    """返回 (platform, normalized_base)。platform 为 None 表示无法识别。"""
    base = normalize_base(url)

    # 1) Shopify: /products.json?limit=1 返回含 products 键的 JSON
    r = _safe_get(f"{base}/products.json?limit=1")
    if r is not None and r.status_code == 200:
        try:
            if isinstance(r.json().get("products"), list):
                return "shopify", base
        except Exception:
            pass

    # 2) 通用 sitemap
    r = _safe_get(f"{base}/sitemap.xml")
    if r is not None and r.status_code == 200 and "<url" in (r.text or ""):
        return "generic", base
    r = _safe_get(f"{base}/robots.txt")
    if r is not None and r.status_code == 200 and "sitemap" in (r.text or "").lower():
        return "generic", base

    return None, base
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_detect_platform.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/crawlers/detect.py backend/tests/test_detect_platform.py
git commit -m "feat(tracking): add detect_platform (shopify/sitemap probe)"
```

---

## Task 3: tracking API —— GET 列表

**Files:**
- Create: `backend/app/api/tracking.py`
- Modify: `backend/app/main.py`（注册 router）
- Test: `backend/tests/test_tracking_api.py`（追加）

参考已有：`backend/app/api/routes.py` 的 `_current_workspace`(242)、`_require_admin`(143)、`_workspace_site_names`(287)、`require_user`、`product_dict`。tracking 路由复用同样的鉴权依赖。

- [ ] **Step 1: 写失败测试（追加到 test_tracking_api.py）**

```python
from fastapi.testclient import TestClient

from app.main import app
from app.auth import make_token


def _admin_headers():
    return {"Authorization": f"Bearer {make_token('admin', '')}"}


def test_tracking_list_requires_auth():
    init_db()
    client = TestClient(app)
    assert client.get("/api/tracking").status_code == 401


def test_tracking_list_returns_items_shape():
    init_db()
    client = TestClient(app)
    r = client.get("/api/tracking", headers=_admin_headers())
    assert r.status_code == 200
    body = r.json()
    assert "items" in body and "total" in body
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_tracking_api.py -k tracking_list -v`
Expected: FAIL（404，路由未注册）

- [ ] **Step 3: 写 tracking.py（GET 列表）**

新建 `backend/app/api/tracking.py`：

```python
"""标杆网站维护面板 API —— /api/tracking*。

扩展 Site 表的追踪元数据 CRUD + 贴 URL 探测建站 + 触发抓取。
写操作 admin 门控,读列表登录即可,均限当前 workspace 作用域。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Product, Site, WorkspaceSite
from .routes import (require_user, _current_workspace, _require_admin,
                     _workspace_site_names)

router = APIRouter(prefix="/api", dependencies=[Depends(require_user)])

_NEW_DAYS = 30


def _metrics(db: Session, site: str) -> dict:
    """实时算 products(distinct spu)/30天销量/收入,不冗余存储。"""
    products = (db.query(func.count(func.distinct(Product.spu)))
                .filter(Product.site == site).scalar() or 0)
    sales, revenue = db.query(
        func.coalesce(func.sum(Product.thirty_day_sales), 0),
        func.coalesce(func.sum(Product.thirty_day_revenue), 0.0),
    ).filter(Product.site == site).first()
    return {"products": int(products),
            "thirty_day_sales": int(sales or 0),
            "thirty_day_revenue": round(revenue or 0, 2)}


def tracking_row(db: Session, s: Site) -> dict:
    m = _metrics(db, s.site)
    return {
        "site": s.site, "brand": s.brand, "country": s.country,
        "url": s.url, "platform": s.platform,
        "track_status": s.track_status or "tracking",
        "source": s.source or "yaml", "creator": s.creator,
        "review_rate": s.review_rate,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
        "last_crawled": s.last_crawled.isoformat() if s.last_crawled else None,
        **m,
    }


@router.get("/tracking")
def list_tracking(
    search: str | None = None,
    market: str | None = None,
    brand: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 10,
    user: str = Depends(require_user),
    x_workspace_id: str | None = Header(default=None, alias="X-Workspace-ID"),
    db: Session = Depends(get_db),
):
    ws = _current_workspace(user, db, x_workspace_id)
    allowed = _workspace_site_names(db, ws.id, include_hidden=True)
    q = db.query(Site).filter(Site.site.in_(allowed))
    if search:
        like = f"%{search}%"
        q = q.filter(or_(Site.url.ilike(like), Site.brand.ilike(like),
                         Site.site.ilike(like)))
    if market:
        q = q.filter(Site.country == market)
    if brand:
        q = q.filter(Site.brand == brand)
    if status:
        q = q.filter(Site.track_status == status)
    total = q.count()
    rows = (q.order_by(Site.created_at.desc().nullslast(), Site.id.desc())
            .offset((page - 1) * page_size).limit(page_size).all())
    return {"total": total, "page": page, "page_size": page_size,
            "items": [tracking_row(db, s) for s in rows]}
```

注意：`_workspace_site_names` 现签名为 `(db, workspace_id, include_hidden=False)`（见 routes.py:287）。确认其支持 `include_hidden` 关键字；若不支持，改调 `_workspace_site_names(db, ws.id)`。

- [ ] **Step 4: 注册 router**

`backend/app/main.py` 中，找到 `app.include_router(api_router)` 一行（其他 router 注册处），其后加：

```python
from .api.tracking import router as tracking_router
app.include_router(tracking_router)
```

（import 放文件顶部 import 区更佳；与现有 `from .api.routes import ...` 风格一致即可。）

- [ ] **Step 5: 跑测试确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_tracking_api.py -k tracking_list -v`
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/tracking.py backend/app/main.py backend/tests/test_tracking_api.py
git commit -m "feat(tracking): GET /api/tracking list endpoint"
```

---

## Task 4: tracking API —— POST 建站（探测+触发）

**Files:**
- Modify: `backend/app/api/tracking.py`
- Test: `backend/tests/test_tracking_api.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
from unittest.mock import patch


def test_add_tracking_creates_site_and_enqueues():
    init_db()
    client = TestClient(app)
    with patch("app.api.tracking.detect_platform",
               return_value=("shopify", "https://newbrand.example.com")), \
         patch("app.api.tracking.enqueue", return_value=999) as enq:
        r = client.post("/api/tracking",
                        headers=_admin_headers(),
                        json={"url": "https://newbrand.example.com/x", "brand": "NewBrand", "country": "US"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["platform"] == "shopify"
    assert body["source"] == "user"
    assert body["track_status"] == "tracking"
    enq.assert_called_once()
    # 站已落库
    assert client.get("/api/tracking", headers=_admin_headers()).json()["total"] >= 1


def test_add_tracking_400_when_undetectable():
    init_db()
    client = TestClient(app)
    with patch("app.api.tracking.detect_platform",
               return_value=(None, "https://static.example.com")):
        r = client.post("/api/tracking", headers=_admin_headers(),
                        json={"url": "https://static.example.com"})
    assert r.status_code == 400


def test_add_tracking_forbidden_for_non_admin():
    init_db()
    client = TestClient(app)
    r = client.post("/api/tracking",
                    headers={"Authorization": f"Bearer {make_token('viewer_user', '')}"},
                    json={"url": "https://x.example.com"})
    assert r.status_code in (401, 403)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_tracking_api.py -k add_tracking -v`
Expected: FAIL（405/404，POST 未实现）

- [ ] **Step 3: 实现 POST + site code 生成**

在 `tracking.py` 顶部 import 区追加：

```python
import re
from urllib.parse import urlparse

from ..crawlers.detect import detect_platform
from ..runner import enqueue
```

追加 helper + 端点：

```python
def _gen_site_code(db: Session, base: str, country: str | None) -> str:
    """从 host 主域 + country 后缀生成唯一 site 主键(如 newbrand_us)。"""
    host = urlparse(base).netloc.split(":")[0]
    parts = [p for p in host.split(".") if p not in ("www", "com", "co", "shop")]
    stem = re.sub(r"[^a-z0-9]", "", (parts[0] if parts else "site").lower()) or "site"
    suffix = (country or "xx").lower()[:2]
    code = f"{stem}_{suffix}"
    n = 2
    while db.query(Site).filter(Site.site == code).first():
        code = f"{stem}_{suffix}{n}"
        n += 1
    return code


@router.post("/tracking")
def add_tracking(
    payload: dict,
    user: str = Depends(require_user),
    x_workspace_id: str | None = Header(default=None, alias="X-Workspace-ID"),
    db: Session = Depends(get_db),
):
    _require_admin(user, db)
    ws = _current_workspace(user, db, x_workspace_id)
    raw_url = (payload.get("url") or "").strip()
    if not raw_url:
        raise HTTPException(400, "url 不能为空")
    if len(raw_url) > 150:
        raise HTTPException(400, "URL 上限 150 字符")
    brand = (payload.get("brand") or "").strip()[:50] or None
    country = (payload.get("country") or "").strip()[:8] or None

    platform, base = detect_platform(raw_url)
    if platform is None:
        raise HTTPException(400, "无法识别平台，请联系技术人员手工配置")

    code = _gen_site_code(db, base, country)
    now = datetime.utcnow()
    site = Site(site=code, brand=brand, country=country, url=base,
                platform=platform, proxy_tier="none",
                track_status="tracking", source="user",
                creator=user, created_at=now, updated_at=now)
    db.add(site)
    # 加入当前 workspace
    db.add(WorkspaceSite(workspace_id=ws.id, site=code,
                         display_name=f"{brand or code} · {country or ''}".strip(" ·"),
                         enabled=True, hidden=False, sort_order=0))
    db.commit()
    db.refresh(site)

    # 触发一次抓取(复用现有 enqueue/worker)
    try:
        enqueue(code, trigger="tracking_add",
                requested_by_workspace_id=ws.id)
    except Exception:
        pass  # 入队失败不阻断建站,站已落库,可后续手动触发

    return tracking_row(db, site)
```

注意：`enqueue` 第三参 `requested_by_user_id` 可选；只传 workspace 即可。`enqueue` 内部会校验 Site 已存在（本流程已 commit，OK）。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_tracking_api.py -k add_tracking -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/tracking.py backend/tests/test_tracking_api.py
git commit -m "feat(tracking): POST /api/tracking add+detect+enqueue"
```

---

## Task 5: tracking API —— PATCH/pause/resume/DELETE

**Files:**
- Modify: `backend/app/api/tracking.py`
- Test: `backend/tests/test_tracking_api.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
def _make_user_site(client):
    """借 POST 建一个 source=user 的站,返回其 site code。"""
    with patch("app.api.tracking.detect_platform",
               return_value=("shopify", "https://edit.example.com")), \
         patch("app.api.tracking.enqueue", return_value=1):
        r = client.post("/api/tracking", headers=_admin_headers(),
                        json={"url": "https://edit.example.com", "brand": "B", "country": "US"})
    return r.json()["site"]


def test_patch_edits_brand_and_review_rate():
    init_db(); client = TestClient(app)
    code = _make_user_site(client)
    r = client.patch(f"/api/tracking/{code}", headers=_admin_headers(),
                     json={"brand": "Edited", "review_rate": 0.03})
    assert r.status_code == 200
    assert r.json()["brand"] == "Edited"
    assert r.json()["review_rate"] == 0.03


def test_pause_and_resume():
    init_db(); client = TestClient(app)
    code = _make_user_site(client)
    assert client.post(f"/api/tracking/{code}/pause", headers=_admin_headers()).json()["track_status"] == "paused"
    assert client.post(f"/api/tracking/{code}/resume", headers=_admin_headers()).json()["track_status"] == "tracking"


def test_delete_only_user_source():
    init_db(); client = TestClient(app)
    code = _make_user_site(client)
    assert client.delete(f"/api/tracking/{code}", headers=_admin_headers()).status_code == 200
    # 种子站(source=yaml)不可删 → 取一个已存在的 yaml 站
    from app.db import SessionLocal
    s = SessionLocal()
    yaml_site = s.query(__import__('app.models', fromlist=['Site']).Site).filter_by(source="yaml").first()
    s.close()
    if yaml_site:
        assert client.delete(f"/api/tracking/{yaml_site.site}", headers=_admin_headers()).status_code == 400
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_tracking_api.py -k "patch_edits or pause_and or delete_only" -v`
Expected: FAIL（405/404）

- [ ] **Step 3: 实现 PATCH/pause/resume/DELETE**

在 `tracking.py` 追加：

```python
def _user_site_or_404(db: Session, ws_id: int, code: str) -> Site:
    allowed = set(_workspace_site_names(db, ws_id, include_hidden=True))
    if code not in allowed:
        raise HTTPException(404, "站点不存在或不在当前工作区")
    site = db.query(Site).filter(Site.site == code).first()
    if not site:
        raise HTTPException(404, "站点不存在")
    return site


@router.patch("/tracking/{code}")
def edit_tracking(code: str, payload: dict,
                  user: str = Depends(require_user),
                  x_workspace_id: str | None = Header(default=None, alias="X-Workspace-ID"),
                  db: Session = Depends(get_db)):
    _require_admin(user, db)
    ws = _current_workspace(user, db, x_workspace_id)
    site = _user_site_or_404(db, ws.id, code)
    if "brand" in payload:
        site.brand = (payload.get("brand") or "").strip()[:50] or None
    if "country" in payload:
        site.country = (payload.get("country") or "").strip()[:8] or None
    if "review_rate" in payload:
        rr = payload.get("review_rate")
        site.review_rate = float(rr) if rr not in (None, "") else None
    site.updated_at = datetime.utcnow()
    db.commit(); db.refresh(site)
    return tracking_row(db, site)


def _set_status(db, ws_id, code, status):
    site = _user_site_or_404(db, ws_id, code)
    site.track_status = status
    site.updated_at = datetime.utcnow()
    db.commit(); db.refresh(site)
    return tracking_row(db, site)


@router.post("/tracking/{code}/pause")
def pause_tracking(code: str, user: str = Depends(require_user),
                   x_workspace_id: str | None = Header(default=None, alias="X-Workspace-ID"),
                   db: Session = Depends(get_db)):
    _require_admin(user, db)
    ws = _current_workspace(user, db, x_workspace_id)
    return _set_status(db, ws.id, code, "paused")


@router.post("/tracking/{code}/resume")
def resume_tracking(code: str, user: str = Depends(require_user),
                    x_workspace_id: str | None = Header(default=None, alias="X-Workspace-ID"),
                    db: Session = Depends(get_db)):
    _require_admin(user, db)
    ws = _current_workspace(user, db, x_workspace_id)
    return _set_status(db, ws.id, code, "tracking")


@router.delete("/tracking/{code}")
def delete_tracking(code: str, user: str = Depends(require_user),
                    x_workspace_id: str | None = Header(default=None, alias="X-Workspace-ID"),
                    db: Session = Depends(get_db)):
    _require_admin(user, db)
    ws = _current_workspace(user, db, x_workspace_id)
    site = _user_site_or_404(db, ws.id, code)
    if (site.source or "yaml") != "user":
        raise HTTPException(400, "种子站点不可删除")
    db.query(WorkspaceSite).filter(WorkspaceSite.site == code).delete()
    db.delete(site)
    db.commit()
    return {"deleted": code}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_tracking_api.py -v`
Expected: 全部 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/tracking.py backend/tests/test_tracking_api.py
git commit -m "feat(tracking): PATCH/pause/resume/DELETE endpoints"
```

---

## Task 6: pause 生效 + 抓取异常置 error

**Files:**
- Modify: `backend/app/scheduler.py:30-38`（`_product_job`）
- Modify: `backend/app/runner.py:107-120`（execute_job 收尾）
- Test: `backend/tests/test_tracking_api.py`（追加逻辑单测）

- [ ] **Step 1: 写失败测试**

```python
def test_paused_site_skips_enqueue():
    init_db(); client = TestClient(app)
    code = _make_user_site(client)
    client.post(f"/api/tracking/{code}/pause", headers=_admin_headers())
    from app.scheduler import _product_job
    with patch("app.runner.enqueue") as enq:
        _product_job(code)
        enq.assert_not_called()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_tracking_api.py -k paused_site -v`
Expected: FAIL（当前 `_product_job` 无条件 enqueue）

- [ ] **Step 3: 改 `_product_job` 加 paused 跳过**

`backend/app/scheduler.py` 的 `_product_job`（第 30 行）改为：

```python
def _product_job(site_name: str) -> None:
    """商品站定时采集 —— 入队，由 worker 执行。paused 站跳过。"""
    try:
        from .db import session_scope
        from .models import Site
        with session_scope() as s:
            site = s.query(Site).filter(Site.site == site_name).first()
            if site and site.track_status == "paused":
                logger.info("站点 %s 已暂停追踪,跳过定时采集", site_name)
                return
        from .runner import enqueue
        job_id = enqueue(site_name, trigger="scheduled")
        logger.info("已入队商品采集: %s (job %s)", site_name, job_id)
    except Exception as exc:
        logger.error("入队失败 %s: %s", site_name, exc)
```

- [ ] **Step 4: 改 runner 收尾置 error/复位**

`backend/app/runner.py` 的 execute_job 成功收尾处（第 119 行 `site.last_crawled = datetime.utcnow()` 旁）改为：

```python
        site = s.query(Site).filter(Site.site == site_name).first()
        site.last_crawled = datetime.utcnow()
        site.updated_at = datetime.utcnow()
        # 抓到 0 product 视为异常;不覆盖用户手动 paused
        produced = stats["inserted"] + stats["updated"]
        if site.track_status != "paused":
            site.track_status = "error" if produced == 0 else "tracking"
```

并在 execute_job 的 `failed` 分支（第 92-93 行 `job.status = "failed"` 处）补置 error。在该分支的 `job.finished_at = datetime.utcnow()` 之后加：

```python
            _fsite = s.query(Site).filter(Site.site == site_name).first()
            if _fsite and _fsite.track_status != "paused":
                _fsite.track_status = "error"
```

- [ ] **Step 5: 跑测试确认通过 + 全量回归**

Run: `cd backend && .venv/bin/python -m pytest tests/test_tracking_api.py -k paused_site -v && .venv/bin/python -m pytest -q`
Expected: paused_site PASS；全量 `N passed`（含原 164 + 新增）

- [ ] **Step 6: Commit**

```bash
git add backend/app/scheduler.py backend/app/runner.py backend/tests/test_tracking_api.py
git commit -m "feat(tracking): pause skips schedule + error status on failed/empty crawl"
```

---

## Task 7: tracking 导出

**Files:**
- Modify: `backend/app/api/tracking.py`
- Test: `backend/tests/test_tracking_api.py`（追加）

复用 `backend/app/export.py::export_workbook`？——它是按 Product 导出的，不匹配追踪列格式。这里直接用 openpyxl 生成追踪列表 xlsx（表头同面板）。

- [ ] **Step 1: 写失败测试**

```python
def test_export_returns_xlsx():
    init_db(); client = TestClient(app)
    _make_user_site(client)
    r = client.get("/api/tracking/export", headers=_admin_headers())
    assert r.status_code == 200
    assert "spreadsheet" in r.headers.get("content-type", "")
    assert r.content[:2] == b"PK"  # xlsx = zip
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_tracking_api.py -k export_returns -v`
Expected: FAIL（404）

- [ ] **Step 3: 实现导出端点**

在 `tracking.py` 追加（import 区加 `import io` 与 `from fastapi.responses import StreamingResponse`）：

```python
@router.get("/tracking/export")
def export_tracking(
    search: str | None = None, market: str | None = None,
    brand: str | None = None, status: str | None = None,
    user: str = Depends(require_user),
    x_workspace_id: str | None = Header(default=None, alias="X-Workspace-ID"),
    db: Session = Depends(get_db),
):
    import io
    from openpyxl import Workbook
    from fastapi.responses import StreamingResponse

    ws = _current_workspace(user, db, x_workspace_id)
    allowed = _workspace_site_names(db, ws.id, include_hidden=True)
    q = db.query(Site).filter(Site.site.in_(allowed))
    if search:
        like = f"%{search}%"
        q = q.filter(or_(Site.url.ilike(like), Site.brand.ilike(like), Site.site.ilike(like)))
    if market:
        q = q.filter(Site.country == market)
    if brand:
        q = q.filter(Site.brand == brand)
    if status:
        q = q.filter(Site.track_status == status)
    rows = q.order_by(Site.created_at.desc().nullslast(), Site.id.desc()).all()

    wb = Workbook(); sh = wb.active; sh.title = "Tracking"
    headers = ["Market", "Brand", "URL", "Status", "Products",
               "30-Day Sales", "30-Day Revenue", "Updated", "Created", "Creator"]
    sh.append(headers)
    for s in rows:
        m = _metrics(db, s.site)
        sh.append([s.country, s.brand, s.url, s.track_status, m["products"],
                   m["thirty_day_sales"], m["thirty_day_revenue"],
                   s.updated_at.isoformat() if s.updated_at else "",
                   s.created_at.isoformat() if s.created_at else "", s.creator])
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=tracking.xlsx"})
```

注意：`export_tracking` 路由必须注册在 `/tracking/{code}` 之后或用静态前缀，避免 `export` 被当成 `{code}` 匹配。**把 `@router.get("/tracking/export")` 定义放在 `@router.patch("/tracking/{code}")` 等动态路由之前**（FastAPI 按定义顺序匹配；静态路径需先注册）。建议在 list_tracking 之后紧接定义 export。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_tracking_api.py -k export_returns -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/tracking.py backend/tests/test_tracking_api.py
git commit -m "feat(tracking): GET /api/tracking/export xlsx"
```

---

## Task 8: 前端 API client + 路由 + 导航

**Files:**
- Create: `frontend-app/src/api/tracking.ts`
- Modify: `frontend-app/src/app/router.ts`
- Modify: `frontend-app/src/components/layout/AppLayout.vue`

- [ ] **Step 1: 写 api/tracking.ts**

新建 `frontend-app/src/api/tracking.ts`：

```ts
import { apiJson, jsonBody, qs } from './client'

export function listTracking(params: Record<string, unknown> = {}) {
  return apiJson(`/api/tracking${qs(params)}`)
}
export function addTracking(payload: { url: string; brand?: string; country?: string }) {
  return apiJson('/api/tracking', { method: 'POST', ...jsonBody(payload) })
}
export function editTracking(site: string, payload: Record<string, unknown>) {
  return apiJson(`/api/tracking/${encodeURIComponent(site)}`, { method: 'PATCH', ...jsonBody(payload) })
}
export function pauseTracking(site: string) {
  return apiJson(`/api/tracking/${encodeURIComponent(site)}/pause`, { method: 'POST' })
}
export function resumeTracking(site: string) {
  return apiJson(`/api/tracking/${encodeURIComponent(site)}/resume`, { method: 'POST' })
}
export function deleteTracking(site: string) {
  return apiJson(`/api/tracking/${encodeURIComponent(site)}`, { method: 'DELETE' })
}
```

- [ ] **Step 2: 注册路由**

`frontend-app/src/app/router.ts`：import 区加 `import TrackingPage from '../pages/TrackingPage.vue'`（Task 9 会建该文件；先建空壳避免 import 失败——见 Task 9 Step 0）。在 `/app` children 数组里 `{ path: 'reports', component: ReportsPage },` 之后加：

```ts
        { path: 'tracking', component: TrackingPage },
```

- [ ] **Step 3: 导航加 tab**

先看 `AppLayout.vue` 现有 nav 项写法：

Run: `grep -n "overview\|reports\|catalog\|to=\|router-link\|nav" frontend-app/src/components/layout/AppLayout.vue | head`

按既有 nav-item 模式，在"站点报表(reports)"项后加一项指向 `/app/tracking`，文案"🎯 标杆维护"。（具体标签/class 跟随该文件现有项的写法，复制一项改 path 与文案。）

- [ ] **Step 4: 不单独提交**，与 Task 9 一起构建后提交（前端三件需一起才能 build 通过）。

---

## Task 9: 前端 TrackingPage.vue

**Files:**
- Create: `frontend-app/src/pages/TrackingPage.vue`

- [ ] **Step 0: 先建最小壳让 build 不炸**

先写一个最小可编译版本，确保 Task 8 的 import 成立，随后在本 Task 内补全。

- [ ] **Step 1: 写完整 TrackingPage.vue**

新建 `frontend-app/src/pages/TrackingPage.vue`。复用本会话已建的 `fmtPrice`、`.title-text` 思路、`canEdit` 模式（loadMe + 角色判断）。国旗用 country code → emoji。

```vue
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { asList, fmtNumber, fmtPrice } from '../api/client'
import { addTracking, deleteTracking, editTracking, listTracking, pauseTracking, resumeTracking } from '../api/tracking'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const rows = ref<Record<string, any>[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const loading = ref(false)
const error = ref('')
const search = ref('')
const fMarket = ref('')
const fBrand = ref('')
const fStatus = ref('')
const showAdd = ref(false)
const addForm = ref({ url: '', brand: '', country: '' })
const addBusy = ref(false)
const editing = ref<Record<string, any> | null>(null)

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / Number(pageSize.value || 10))))
const canEdit = computed(() => {
  const u = auth.user
  if (!u) return false
  return u.global_role === 'super_admin' || ['admin', 'owner'].includes(u.role || '')
})
function flag(cc?: string) {
  if (!cc || cc.length !== 2) return '🌐'
  return String.fromCodePoint(...[...cc.toUpperCase()].map((c) => 127397 + c.charCodeAt(0)))
}
function statusLabel(s?: string) {
  return ({ tracking: 'Tracking', paused: 'Paused', error: '⚠️ 异常' } as Record<string, string>)[s || ''] || s || '—'
}

async function load() {
  loading.value = true; error.value = ''
  try {
    const d = await listTracking({
      search: search.value, market: fMarket.value, brand: fBrand.value,
      status: fStatus.value, page: page.value, page_size: pageSize.value,
    })
    rows.value = asList(d, ['items'])
    total.value = Number(d?.total || rows.value.length || 0)
  } catch (e) { error.value = e instanceof Error ? e.message : String(e) }
  finally { loading.value = false }
}
function applySearch() { page.value = 1; load() }

async function submitAdd() {
  if (!addForm.value.url.trim()) return
  addBusy.value = true; error.value = ''
  try {
    await addTracking({ url: addForm.value.url.trim(), brand: addForm.value.brand.trim() || undefined, country: addForm.value.country.trim() || undefined })
    showAdd.value = false
    addForm.value = { url: '', brand: '', country: '' }
    page.value = 1; await load()
  } catch (e) { error.value = e instanceof Error ? e.message : String(e) }
  finally { addBusy.value = false }
}
async function saveEdit() {
  if (!editing.value) return
  try {
    await editTracking(editing.value.site, { brand: editing.value.brand, country: editing.value.country, review_rate: editing.value.review_rate === '' ? null : Number(editing.value.review_rate) })
    editing.value = null; await load()
  } catch (e) { error.value = e instanceof Error ? e.message : String(e) }
}
async function togglePause(row: Record<string, any>) {
  try {
    if (row.track_status === 'paused') await resumeTracking(row.site)
    else await pauseTracking(row.site)
    await load()
  } catch (e) { error.value = e instanceof Error ? e.message : String(e) }
}
async function remove(row: Record<string, any>) {
  if (!window.confirm(`确认删除追踪「${row.brand || row.site}」？此操作不可撤销。`)) return
  try { await deleteTracking(row.site); await load() }
  catch (e) { error.value = e instanceof Error ? e.message : String(e) }
}
function reportHref(row: Record<string, any>) {
  const p = new URLSearchParams({ site: row.site })
  if (auth.workspaceId) p.set('workspace_id', auth.workspaceId)
  return `/report?${p.toString()}`
}
function exportUrl() {
  const p = new URLSearchParams({ search: search.value, market: fMarket.value, brand: fBrand.value, status: fStatus.value, token: auth.token })
  if (auth.workspaceId) p.set('workspace_id', auth.workspaceId)
  return `/api/tracking/export?${p.toString()}`
}

onMounted(async () => {
  if (auth.token && !auth.user) await auth.loadMe().catch(() => null)
  await load()
})
</script>

<template>
  <section>
    <div class="lead">标杆网站维护</div>
    <div class="sub">{{ loading ? '加载中' : total + ' 个追踪站点' }}</div>
    <UAlert v-if="error" color="error" variant="soft" :title="error" class="mb-4" />

    <div class="tk-toolbar">
      <button v-if="canEdit" class="btn-prim" @click="showAdd = true">+ Add Tracking</button>
      <input class="tk-in" v-model="search" placeholder="🔍 URL / Brand" @keyup.enter="applySearch" />
      <input class="tk-in" v-model="fMarket" placeholder="Market (US/DE…)" @keyup.enter="applySearch" />
      <input class="tk-in" v-model="fBrand" placeholder="Brand" @keyup.enter="applySearch" />
      <select class="tk-in" v-model="fStatus" @change="applySearch">
        <option value="">全部状态</option><option value="tracking">Tracking</option>
        <option value="paused">Paused</option><option value="error">异常</option>
      </select>
      <button class="btn-muted" @click="applySearch">筛选</button>
      <a class="btn-muted" :href="exportUrl()" target="_blank">📥 导出</a>
    </div>

    <table class="tk-table">
      <thead><tr>
        <th>Market</th><th>Brand</th><th>URL</th><th>Status</th><th>Products</th>
        <th>30-Day Sales</th><th>30-Day Revenue</th><th>Updated</th><th>Created</th><th>Creator</th><th>操作</th>
      </tr></thead>
      <tbody>
        <tr v-for="r in rows" :key="r.site">
          <td>{{ flag(r.country) }} {{ r.country || '—' }}</td>
          <td>{{ r.brand || '—' }}</td>
          <td><a class="title-text" :href="r.url" target="_blank" rel="noopener" :title="r.url">{{ r.url }}</a></td>
          <td><span class="tk-badge" :class="r.track_status">{{ statusLabel(r.track_status) }}</span></td>
          <td>{{ fmtNumber(r.products) }}</td>
          <td>{{ fmtNumber(r.thirty_day_sales) }}</td>
          <td>{{ fmtPrice(r.thirty_day_revenue, undefined) }}</td>
          <td>{{ (r.updated_at || '').replace('T', ' ').slice(0, 16) || '—' }}</td>
          <td>{{ (r.created_at || '').replace('T', ' ').slice(0, 16) || '—' }}</td>
          <td>{{ r.creator || '—' }}</td>
          <td class="tk-actions">
            <a :href="reportHref(r)" target="_blank" rel="noopener" class="btn-mini">报告</a>
            <template v-if="canEdit">
              <button class="btn-mini" @click="editing = { ...r }">编辑</button>
              <button class="btn-mini" @click="togglePause(r)">{{ r.track_status === 'paused' ? '恢复' : '暂停' }}</button>
              <button v-if="r.source === 'user'" class="btn-mini btn-danger" @click="remove(r)">删除</button>
            </template>
          </td>
        </tr>
        <tr v-if="!rows.length"><td colspan="11" class="tk-empty">暂无追踪站点</td></tr>
      </tbody>
    </table>

    <div class="pagination">
      <button @click="page = Math.max(1, page - 1); load()" :disabled="page <= 1">‹</button>
      <span>{{ page }} / {{ totalPages }}</span>
      <button @click="page = Math.min(totalPages, page + 1); load()" :disabled="page >= totalPages">›</button>
      <select v-model="pageSize" @change="page = 1; load()">
        <option :value="10">10</option><option :value="20">20</option><option :value="50">50</option>
        <option :value="100">100</option><option :value="200">200</option>
      </select>
    </div>

    <!-- Add 弹窗 -->
    <div v-if="showAdd" class="tk-modal" @click.self="showAdd = false">
      <div class="tk-card">
        <h3>+ Add Tracking</h3>
        <label>URL<input v-model="addForm.url" placeholder="https://brand.example.com" /></label>
        <label>Brand（选填）<input v-model="addForm.brand" maxlength="50" /></label>
        <label>Market（选填，如 US）<input v-model="addForm.country" maxlength="8" /></label>
        <div class="tk-card-foot">
          <button class="btn-muted" @click="showAdd = false">取消</button>
          <button class="btn-prim" :disabled="addBusy" @click="submitAdd">{{ addBusy ? '探测中…' : '添加并抓取' }}</button>
        </div>
      </div>
    </div>

    <!-- Edit 弹窗 -->
    <div v-if="editing" class="tk-modal" @click.self="editing = null">
      <div class="tk-card">
        <h3>编辑追踪</h3>
        <label>Brand<input v-model="editing.brand" maxlength="50" /></label>
        <label>Market<input v-model="editing.country" maxlength="8" /></label>
        <label>留评率 review_rate<input v-model="editing.review_rate" type="number" step="0.001" /></label>
        <div class="tk-card-foot">
          <button class="btn-muted" @click="editing = null">取消</button>
          <button class="btn-prim" @click="saveEdit">保存</button>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.tk-toolbar { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:14px; align-items:center; }
.tk-in { padding:6px 10px; border:1px solid #d1d5db; border-radius:7px; font-size:13px; font-family:inherit; }
.tk-table { width:100%; border-collapse:collapse; font-size:13px; }
.tk-table th, .tk-table td { text-align:left; padding:10px 12px; border-bottom:1px solid #f0f1f3; }
.title-text { display:inline-block; max-width:240px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; vertical-align:bottom; }
.tk-badge { padding:2px 8px; border-radius:9px; font-size:11px; font-weight:700; }
.tk-badge.tracking { background:#dcfce7; color:#166534; }
.tk-badge.paused { background:#f3f4f6; color:#6b7280; }
.tk-badge.error { background:#fee2e2; color:#991b1b; }
.tk-actions { display:flex; gap:6px; flex-wrap:wrap; }
.btn-mini { padding:3px 8px; border:1px solid #d1d5db; border-radius:6px; background:#fff; cursor:pointer; font-size:12px; }
.btn-mini.btn-danger { color:#b91c1c; border-color:#fecaca; }
.tk-empty { text-align:center; color:#9ca3af; padding:28px; }
.tk-modal { position:fixed; inset:0; background:rgba(0,0,0,.4); display:flex; align-items:center; justify-content:center; z-index:100; }
.tk-card { background:#fff; border-radius:12px; padding:22px; width:420px; max-width:92vw; display:flex; flex-direction:column; gap:12px; }
.tk-card label { display:flex; flex-direction:column; gap:4px; font-size:12.5px; color:#6b7280; }
.tk-card input, .tk-card select { padding:7px 10px; border:1px solid #d1d5db; border-radius:7px; font-size:13px; font-family:inherit; }
.tk-card-foot { display:flex; justify-content:flex-end; gap:8px; margin-top:6px; }
.pagination { display:flex; align-items:center; gap:8px; justify-content:center; margin-top:14px; }
</style>
```

> 若 `.btn-prim`/`.btn-muted` 是全局类（其他页用了），保留；否则在本组件 style 补最小定义。构建时若 vue-tsc 报这些类无关紧要（class 不参与类型检查）。

- [ ] **Step 2: 构建确认通过**

Run: `cd frontend-app && pnpm build 2>&1 | tail -8`
Expected: `vue-tsc --noEmit` 无错 + bundle 产出

- [ ] **Step 3: Commit（前端三件一起）**

```bash
git add frontend-app/src/api/tracking.ts frontend-app/src/pages/TrackingPage.vue frontend-app/src/app/router.ts frontend-app/src/components/layout/AppLayout.vue
git commit -m "feat(tracking): TrackingPage.vue + api client + route + nav"
```

---

## Task 10: 端到端本地验证

**Files:** 无（验证）

- [ ] **Step 1: 后端全量回归**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: 全 passed（原 164 + 新增 tracking/detect 测试）

- [ ] **Step 2: 起服务 + 走完整 CRUD**

```bash
cd backend && .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8099 >/tmp/tk.log 2>&1 &
sleep 5
TK=$(curl -s -X POST http://127.0.0.1:8099/api/login -H 'Content-Type: application/json' -d '{"username":"admin","password":"<admin密码>"}' | python3 -c "import json,sys;print(json.load(sys.stdin).get('token',''))")
# 列表
curl -s "http://127.0.0.1:8099/api/tracking?page_size=5" -H "Authorization: Bearer $TK" | head -c 400
# 导出
curl -s -o /tmp/tk.xlsx -w "export=%{http_code} type=%{content_type}\n" "http://127.0.0.1:8099/api/tracking/export" -H "Authorization: Bearer $TK"
pkill -f "uvicorn app.main:app --host 127.0.0.1 --port 8099"
```
Expected: 列表返回 items/total；export=200 且 type 含 spreadsheet。
（admin 密码本地未知则跳过登录态验证，依赖单测已覆盖逻辑。）

- [ ] **Step 3: 前端 SPA serve 验证**

构建后 `dist` 已生成；起后端访问 `/app`，确认 bundle 200、`/app/tracking` 路由可达（SPA 路由，curl `/app` 拿 bundle 即可）。

Run: `cd frontend-app && pnpm build && cd ../backend && .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8099 >/tmp/tk2.log 2>&1 & sleep 5; curl -s http://127.0.0.1:8099/app | grep -oE 'assets/index-[A-Za-z0-9_-]+\.js' | head -1; pkill -f "port 8099"`
Expected: 输出哈希 bundle 名

- [ ] **Step 4: 更新 memory**

把"标杆维护面板已实现"追加进 `acceptance-report-rootcause.md`（D 档完成、能力边界、未部署）。

- [ ] **Step 5: 不自动部署**。汇报完成,等用户决定 commit/部署。

---

## Self-Review 检查（写计划者已核对）

- **Spec 覆盖**：§1 模型→Task1；§2 探测→Task2；§3 API(GET/POST/PATCH/pause/resume/DELETE/export)→Task3-5,7；pause 生效+error 置位→Task6；§4 前端→Task8-9；§5 权限→各写端点 `_require_admin` + 前端 `canEdit`。全覆盖。
- **能力边界**：探测仅 shopify/generic（Task2 实现与注释一致）。
- **类型/签名一致**：`detect_platform→(str|None,str)`、`tracking_row(db,site)`、`_metrics(db,site)`、`_user_site_or_404(db,ws_id,code)` 跨 Task 一致；前端 `listTracking/addTracking/...` 与后端路径一致。
- **已知风险**：`_workspace_site_names` 的 `include_hidden` 关键字需在 Task3 Step3 核实（routes.py:287 签名）；FastAPI 静态路由 `/tracking/export` 必须先于 `/tracking/{code}` 注册（Task7 Step3 已标注）。
