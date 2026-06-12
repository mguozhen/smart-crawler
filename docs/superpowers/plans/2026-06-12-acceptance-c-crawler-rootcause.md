# C 档 crawler 根因修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复验收报告"爬取数据"偏差的三条结构性根因——per-site `max_products` 失效、促销永不触发、变体当 SKU 多算——纯本地代码 + 测试，无迁移无重爬。

**Architecture:** (1) `BaseCrawler` 加 `_resolve_limit()` 统一入口，22 个 crawler 的 `self.limit` 接进去；(2) 删 `pipeline.normalize` 的 original_price 回填；(3) `list_sites` 端点用 `count(distinct coalesce(spu,sku))` 真算 spu_count。每条根因先写失败测试。

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy, pytest（`pytest.mark.unit`），SQLite in-memory 测试。

---

## File Structure

- `backend/app/crawlers/base.py` — 新增 `_resolve_limit()`（max_products 统一入口）
- `backend/app/crawlers/*.py` (×22 + magento/shoper/generic 收编) — `self.limit` 改走 `_resolve_limit`
- `backend/app/pipeline.py` — 删除 original_price 回填两行
- `backend/app/api/routes.py` — `list_sites` 的 `spu_counts` 真算
- `backend/tests/test_crawler_limit.py` — 新建，根因 1
- `backend/tests/test_pipeline_promo.py` — 新建，根因 2
- `backend/tests/test_site_spu_count.py` — 新建，根因 3

测试约定（照 `tests/test_workspace_tenancy.py`）：模块级 `pytestmark = pytest.mark.unit`；`_session()` 用 `sqlite:///:memory:` + `Base.metadata.create_all`；端点直接函数调用（非 TestClient）。

---

## Task 1：根因 2 — 删除促销回填（最小、零依赖，先做）

**Files:**
- Create: `backend/tests/test_pipeline_promo.py`
- Modify: `backend/app/pipeline.py:71-72`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_pipeline_promo.py`:
```python
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Product
from app.pipeline import normalize
from app.runner import _detect_promotions

pytestmark = pytest.mark.unit


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return Session()


def test_normalize_keeps_original_none_when_missing():
    p = normalize({"sku": "S1", "title": "t", "product_url": "u",
                   "site": "x", "sale_price": 10})
    assert p["original_price"] is None


def test_normalize_preserves_real_original():
    p = normalize({"sku": "S1", "title": "t", "product_url": "u",
                   "site": "x", "sale_price": 10, "original_price": 20})
    assert p["original_price"] == 20.0


def test_detect_promotions_only_fires_on_real_discount():
    db = _session()
    # A: 有真实折扣 original 20 > sale 10
    db.add(Product(site="x", sku="A", title="A", sale_price=10.0,
                   original_price=20.0, status="on_sale"))
    # B: 仅 sale，无 original（回填已删 → original 应为 None，不算促销）
    db.add(Product(site="x", sku="B", title="B", sale_price=10.0,
                   original_price=None, status="on_sale"))
    db.commit()
    n = _detect_promotions(db, "x")
    assert n == 1
    from app.models import Promotion
    skus = [r.sku for r in db.query(Promotion).filter(Promotion.site == "x").all()]
    assert skus == ["A"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_pipeline_promo.py -v`
Expected: `test_normalize_keeps_original_none_when_missing` FAIL（当前回填使 original==10.0），`test_detect_promotions_only_fires_on_real_discount` FAIL（B 被回填后 original==sale，SQL `>` 不触发但 B original 来自入库值 None... 实际当前 normalize 不参与这里）。至少第一个断言失败。

- [ ] **Step 3: 删除回填**

`backend/app/pipeline.py`，删除第 71-72 行：
```python
    if p.get("original_price") is None:
        p["original_price"] = p.get("sale_price")
```
（删除后 `normalize` 末尾直接 `return p`。）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && python -m pytest tests/test_pipeline_promo.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add backend/tests/test_pipeline_promo.py backend/app/pipeline.py
git commit -m "fix(pipeline): 不再把缺失 original_price 回填为 sale_price · 促销可真触发"
```

---

## Task 2：根因 3 — list_sites 真算 spu_count

**Files:**
- Create: `backend/tests/test_site_spu_count.py`
- Modify: `backend/app/api/routes.py`（`list_sites` 内 `spu_counts = sku_counts` 一行）

- [ ] **Step 1: 写失败测试**

`backend/tests/test_site_spu_count.py`:
```python
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (Product, Site, User, Workspace, WorkspaceSite)

pytestmark = pytest.mark.unit


def _session():
    from app.api.routes import _COVERAGE_CACHE
    _COVERAGE_CACHE.clear()
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return Session()


def _ws(db, slug):
    w = Workspace(name=slug, slug=slug, type="customer", status="active")
    db.add(w); db.flush(); return w


def _user(db, name, ws):
    u = User(username=name, workspace_id=ws.id, role="user", status="active")
    db.add(u); db.flush(); return u


def _site(db, ws, site):
    db.add(Site(site=site, url=f"https://{site}.com", country="US",
                platform="shopify", proxy_tier="dc"))
    db.add(WorkspaceSite(workspace_id=ws.id, site=site))
    db.flush()


def _prod(db, site, sku, spu):
    db.add(Product(site=site, sku=sku, spu=spu, title=sku, sale_price=1.0,
                   status="on_sale"))


def test_spu_count_dedups_variants_and_coalesces_null():
    db = _session()
    ws = _ws(db, "w1")
    _user(db, "alice", ws)
    # site v: 3 SKU、2 个共享 spu=P1 → spu_count 2
    _site(db, ws, "v")
    _prod(db, "v", "v-1", "P1")
    _prod(db, "v", "v-2", "P1")
    _prod(db, "v", "v-3", "P2")
    # site n: 2 SKU 均 spu=None → coalesce(sku) 兜底 → spu_count 2
    _site(db, ws, "n")
    _prod(db, "n", "n-1", None)
    _prod(db, "n", "n-2", None)
    db.commit()

    from app.api.routes import list_sites
    rows = list_sites(user="alice", x_workspace_id=str(ws.id), db=db)
    by = {r["site"]: r for r in rows}
    assert by["v"]["sku_count"] == 3
    assert by["v"]["spu_count"] == 2
    assert by["n"]["sku_count"] == 2
    assert by["n"]["spu_count"] == 2
```

> 注：`_user`/`_site` 字段以实际 `app/models.py` 为准；若 `User`/`Site` 必填字段更多，参照 `tests/test_workspace_tenancy.py` 的 `_user`/`_site` helper 补齐。执行时先核对模型。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_site_spu_count.py -v`
Expected: FAIL — `by["v"]["spu_count"] == 2` 断言失败（当前 spu_count=sku_count=3）

- [ ] **Step 3: 改 list_sites**

`backend/app/api/routes.py`，把 `list_sites` 内：
```python
    # spu_count: 之前 distinct group_by 在大表上跑 7-8s · 改成 sku_count 兜底
    # (sku/spu 比 ~1:1 在 vidaxl 系列 · 客户看的是数量级 · 真要精确可单独查)
    spu_counts = sku_counts
```
改为：
```python
    # spu_count: distinct(coalesce(spu, sku)) · 变体合并、无 spu 行按 sku 各算一款
    # 大表 cache-miss 时约 7-8s · 由 30s _COVERAGE_CACHE 兜底
    spu_counts = dict(
        db.query(Product.site,
                 func.count(func.distinct(func.coalesce(Product.spu, Product.sku))))
          .group_by(Product.site).all())
```
（`func` 已在 `list_sites` 内 `from sqlalchemy import func` 导入，无需新增。）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && python -m pytest tests/test_site_spu_count.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/tests/test_site_spu_count.py backend/app/api/routes.py
git commit -m "fix(routes): list_sites spu_count 真算 distinct(coalesce(spu,sku)) · 变体不再多算"
```

---

## Task 3：根因 1a — BaseCrawler._resolve_limit 入口

**Files:**
- Create: `backend/tests/test_crawler_limit.py`
- Modify: `backend/app/crawlers/base.py`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_crawler_limit.py`:
```python
from __future__ import annotations

import pytest

from app.crawlers.overstock import OverstockCrawler, DEFAULT_LIMIT
from app.models import Site

pytestmark = pytest.mark.unit


def _site():
    return Site(site="x", url="https://x.com", country="US",
                platform="overstock", proxy_tier="dc")


def test_resolve_limit_uses_sites_yaml_max_products(monkeypatch):
    monkeypatch.setattr("app.crawlers.base.get_sites",
                        lambda: [{"site": "x", "max_products": 5}])
    c = OverstockCrawler(_site())
    assert c.limit == 5


def test_resolve_limit_falls_back_to_default(monkeypatch):
    monkeypatch.setattr("app.crawlers.base.get_sites", lambda: [{"site": "x"}])
    c = OverstockCrawler(_site())
    assert c.limit == DEFAULT_LIMIT
```

> 执行前确认 `OverstockCrawler` 类名（`grep "^class" app/crawlers/overstock.py`）。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_crawler_limit.py -v`
Expected: FAIL — `c.limit == 5` 失败（overstock 当前硬编码 DEFAULT_LIMIT，且 `app.crawlers.base.get_sites` 还不存在 → monkeypatch AttributeError）

- [ ] **Step 3: 给 base.py 加 _resolve_limit + import get_sites**

`backend/app/crawlers/base.py`，在文件顶部 import 区加：
```python
from ..config import get_sites
```
在 `BaseCrawler` 类内（`ua` 方法前后任意位置）加：
```python
    def _resolve_limit(self, default: int, explicit: int | None = None) -> int:
        """limit 优先级：显式参数 > sites.yaml max_products > env 默认。"""
        if explicit is not None:
            return explicit
        hints = next((c for c in get_sites() if c["site"] == self.site.site), {})
        return int(hints.get("max_products", default))
```

- [ ] **Step 4: 改 overstock 接入（先让测试过）**

`backend/app/crawlers/overstock.py`，`__init__` 内：
```python
        self.limit = DEFAULT_LIMIT
```
改为：
```python
        self.limit = self._resolve_limit(DEFAULT_LIMIT)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd backend && python -m pytest tests/test_crawler_limit.py -v`
Expected: 2 passed

- [ ] **Step 6: 提交**

```bash
git add backend/tests/test_crawler_limit.py backend/app/crawlers/base.py backend/app/crawlers/overstock.py
git commit -m "feat(crawlers): BaseCrawler._resolve_limit · sites.yaml max_products 统一入口"
```

---

## Task 4：根因 1b — Pattern B（9 个无 limit 参数的 crawler）

每个 `__init__` 里 `self.limit = DEFAULT_LIMIT`（或 `STOREFRONT_LIMIT`）→ `self._resolve_limit(...)`。

**Files (Modify):** article, allegro, bol, cdiscount, otto, flexispot, idealo, vonhaus（overstock 已在 Task 3 改过）+ vidaxl（STOREFRONT_LIMIT）

- [ ] **Step 1: 加优先级测试（bol + idealo + vidaxl 抽样）**

追加到 `backend/tests/test_crawler_limit.py`:
```python
def test_bol_reads_max_products(monkeypatch):
    monkeypatch.setattr("app.crawlers.base.get_sites",
                        lambda: [{"site": "x", "max_products": 7}])
    from app.crawlers.bol import BolCrawler
    c = BolCrawler(Site(site="x", url="https://x.com", country="NL",
                        platform="bol", proxy_tier="dc"))
    assert c.limit == 7


def test_idealo_reads_max_products(monkeypatch):
    monkeypatch.setattr("app.crawlers.base.get_sites",
                        lambda: [{"site": "x", "max_products": 9}])
    from app.crawlers.idealo import IdealoCrawler
    c = IdealoCrawler(Site(site="x", url="https://x.com", country="DE",
                           platform="idealo", proxy_tier="dc"))
    assert c.limit == 9


def test_vidaxl_reads_max_products(monkeypatch):
    monkeypatch.setattr("app.crawlers.base.get_sites",
                        lambda: [{"site": "x", "max_products": 11}])
    from app.crawlers.vidaxl import VidaxlCrawler
    c = VidaxlCrawler(Site(site="x", url="https://x.com", country="US",
                           platform="vidaxl", proxy_tier="dc"))
    assert c.limit == 11
```
> 执行前用 `grep "^class" app/crawlers/{bol,idealo,vidaxl}.py` 核对类名。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_crawler_limit.py -v`
Expected: 3 new FAIL

- [ ] **Step 3: 逐个改 9 个文件**

每个文件 `__init__` 内做替换：

article.py / allegro.py / bol.py / cdiscount.py / otto.py / flexispot.py / idealo.py / vonhaus.py:
```python
        self.limit = DEFAULT_LIMIT
```
→
```python
        self.limit = self._resolve_limit(DEFAULT_LIMIT)
```

vidaxl.py:
```python
        self.limit = STOREFRONT_LIMIT
```
→
```python
        self.limit = self._resolve_limit(STOREFRONT_LIMIT)
```

> otto/allegro/idealo 的 `self.scan_cap = SCAN_CAP` 保持不动（不在范围）。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && python -m pytest tests/test_crawler_limit.py -v`
Expected: all passed

- [ ] **Step 5: 提交**

```bash
git add backend/tests/test_crawler_limit.py backend/app/crawlers/{article,allegro,bol,cdiscount,otto,flexispot,idealo,vonhaus,vidaxl}.py
git commit -m "feat(crawlers): 9 个 crawler 接入 _resolve_limit (pattern B)"
```

---

## Task 5：根因 1c — Pattern A（12 个有 limit 参数的 crawler）

每个 `self.limit = limit if limit is not None else DEFAULT_LIMIT` → `self.limit = self._resolve_limit(DEFAULT_LIMIT, limit)`。

**Files (Modify):** aliexpress, bestbuy, etsy, cratebarrel, homary, houzz, ebay, ikea, target, walmart, westelm, wayfair

- [ ] **Step 1: 加测试（cratebarrel 显式参数优先级）**

追加到 `backend/tests/test_crawler_limit.py`:
```python
def test_explicit_limit_param_beats_hints(monkeypatch):
    monkeypatch.setattr("app.crawlers.base.get_sites",
                        lambda: [{"site": "x", "max_products": 5}])
    from app.crawlers.cratebarrel import CrateBarrelCrawler
    c = CrateBarrelCrawler(Site(site="x", url="https://x.com", country="US",
                                platform="cratebarrel", proxy_tier="dc"), limit=3)
    assert c.limit == 3


def test_cratebarrel_reads_hints_when_no_param(monkeypatch):
    monkeypatch.setattr("app.crawlers.base.get_sites",
                        lambda: [{"site": "x", "max_products": 5}])
    from app.crawlers.cratebarrel import CrateBarrelCrawler
    c = CrateBarrelCrawler(Site(site="x", url="https://x.com", country="US",
                                platform="cratebarrel", proxy_tier="dc"))
    assert c.limit == 5
```
> 核对类名 `grep "^class" app/crawlers/cratebarrel.py` 及 `__init__(self, site, limit=None)` 签名。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_crawler_limit.py -v`
Expected: 2 new FAIL（`limit=3` 当前直接走 explicit 分支已对，但 hints 分支失败）

- [ ] **Step 3: 逐个改 12 个文件**

每个文件 `__init__` 内：
```python
        self.limit = limit if limit is not None else DEFAULT_LIMIT
```
→
```python
        self.limit = self._resolve_limit(DEFAULT_LIMIT, limit)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && python -m pytest tests/test_crawler_limit.py -v`
Expected: all passed

- [ ] **Step 5: 提交**

```bash
git add backend/tests/test_crawler_limit.py backend/app/crawlers/{aliexpress,bestbuy,etsy,cratebarrel,homary,houzz,ebay,ikea,target,walmart,westelm,wayfair}.py
git commit -m "feat(crawlers): 12 个 crawler 接入 _resolve_limit (pattern A · 显式参数优先)"
```

---

## Task 6：根因 1d — 收编 magento/shoper/generic

三者已读 `get_sites()` hints，改为复用 `_resolve_limit` 统一（行为等价，去重）。

**Files (Modify):** magento.py, shoper.py, generic.py

- [ ] **Step 1: 改三个文件**

magento.py / shoper.py / generic.py，把：
```python
        hints = next((c for c in get_sites() if c["site"] == site.site), {})
        ...
        self.limit = int(hints.get("max_products", DEFAULT_LIMIT))
```
中的 limit 赋值改为：
```python
        self.limit = self._resolve_limit(DEFAULT_LIMIT)
```
（保留各自 `hints` 变量及其它 `hints.get(...)` 用法如 sitemap/product_match/scan_cap；仅替换 limit 那一行。magento 的 `self.scan_cap = int(hints.get("scan_cap", DEFAULT_SCAN_CAP))` 保持不动。）

- [ ] **Step 2: 加回归测试**

追加到 `backend/tests/test_crawler_limit.py`:
```python
def test_generic_still_reads_max_products(monkeypatch):
    monkeypatch.setattr("app.crawlers.base.get_sites",
                        lambda: [{"site": "x", "max_products": 8,
                                  "sitemap": "https://x.com/sitemap.xml"}])
    from app.crawlers.generic import GenericCrawler
    c = GenericCrawler(Site(site="x", url="https://x.com", country="US",
                            platform="generic", proxy_tier="dc"))
    assert c.limit == 8
```
> 核对类名 `grep "^class" app/crawlers/generic.py`。

- [ ] **Step 3: 跑测试确认通过**

Run: `cd backend && python -m pytest tests/test_crawler_limit.py -v`
Expected: all passed

- [ ] **Step 4: 提交**

```bash
git add backend/tests/test_crawler_limit.py backend/app/crawlers/{magento,shoper,generic}.py
git commit -m "refactor(crawlers): magento/shoper/generic 收编 _resolve_limit"
```

---

## Task 7：全量回归

- [ ] **Step 1: 跑全量 pytest**

Run: `cd backend && python -m pytest -q`
Expected: 全绿（基线 164–180 passed）。若有 import 缺失字段等小问题，按报错补齐测试 helper（参照 tenancy 测试），不改产品代码逻辑。

- [ ] **Step 2: 确认无遗漏 crawler**

Run:
```bash
cd backend && grep -rn "self.limit = " app/crawlers/*.py | grep -v "_resolve_limit\|self.limit = limit\b"
```
Expected: 无输出（除 `self.limit = limit if...` 已被改的形式外，所有赋值都走 `_resolve_limit`）。若有残留，补改并加测试。

- [ ] **Step 3: 最终提交（如有补漏）**

```bash
git add -A && git commit -m "test(acceptance-c): 全量回归绿 + 补漏 crawler limit"
```

---

## Self-Review 记录

- **Spec 覆盖**：根因 1（Task 3-6 覆盖 22+3 crawler）、根因 2（Task 1）、根因 3（Task 2）全部有对应任务。
- **类型一致**：`_resolve_limit(default, explicit=None)` 签名贯穿 Task 3-6；测试统一 monkeypatch `app.crawlers.base.get_sites`。
- **非目标**：scan_cap、PDP 增强、is_new 写路径、真机重爬——明确排除。
- **执行注意**：测试 helper 的 User/Site 必填字段以实际 `app/models.py` 为准，执行时核对；crawler 类名执行前 `grep "^class"` 核对。
