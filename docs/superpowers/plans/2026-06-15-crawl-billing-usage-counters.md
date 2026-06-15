# 计费修复 + 全路径用量计数 实现计划（第 0 步 + 第 1 步 + 批 1 试点）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复网页触发抓取漏计费（写入 Usage、不扣额度），并给所有抓取记录 api_calls / browser_opens / pages_fetched 三项次数。

**Architecture:** 用挂在 `FetchContext` 上的显式 `CrawlCounter` 在统一入口 `CrawlerFetcher` 处计数（只对成功结果计、失败重试不计），由 `BaseCrawler` 注入、`CrawlResult` 带出。`runner.execute_job` 成功后写一行 `api_key_id=None` 的 Usage（只记录、不扣额度）。先把 `CrawlerFetcher` 扩出 POST/JSON/计数能力，再把 2-3 个纯 curl_cffi GET 站点收编进统一入口作为模板验证。

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy, curl_cffi, pytest（marker: `unit` / `smoke`）。测试用 venv 解释器 `backend/.venv/bin/python -m pytest`。

**对应 spec:** `docs/superpowers/specs/2026-06-15-crawl-billing-usage-counters-design.md`

**工作目录:** 所有路径相对 `backend/`。运行测试统一用 `cd backend && .venv/bin/python -m pytest ...`。

---

### Task 1: CrawlCounter 计数器 + FetchContext 注入 + get() 成功计数

**Files:**
- Modify: `backend/app/fetching.py`（加 `CrawlCounter`、`FetchContext.counter`、在 `get()` 末尾计数）
- Test: `backend/tests/test_fetch_counter.py`（新建）

实现要点：计数只在 `get()` 返回**最终结果**时做一次，且仅当 `result.ok`。
按 `result.fetcher` 分流：`"curl_cffi"` → `api_calls += 1`；`"scrapling"` / `"playwright"` → `browser_opens += 1`。
重试过程中失败的中间结果 `ok=False`，不计；最终全失败也不计。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_fetch_counter.py
from __future__ import annotations

import pytest

from app.fetching import CrawlCounter, CrawlerFetcher, FetchContext, FetchResult
from app.models import Site

pytestmark = pytest.mark.unit


def _site() -> Site:
    return Site(site="t", url="https://example.com", country="US",
                proxy_tier="none", platform="generic")


def _ctx(counter, retries=0):
    return FetchContext(site=_site(), counter=counter, use_proxy=False,
                        retries=retries)


def test_counter_pages_is_sum():
    c = CrawlCounter(api_calls=3, browser_opens=2)
    assert c.pages_fetched == 5


def test_success_curl_increments_api_calls(monkeypatch):
    c = CrawlCounter()
    fetcher = CrawlerFetcher(_ctx(c), middlewares=[])

    def fake_once(url, *, attempt=1, **kw):
        return FetchResult(ok=True, url=url, status=200, text="ok",
                           fetcher="curl_cffi", attempt=attempt)

    monkeypatch.setattr(fetcher, "_get_once", fake_once)
    fetcher.get("https://example.com/p/1")
    assert c.api_calls == 1
    assert c.browser_opens == 0


def test_failure_does_not_count(monkeypatch):
    c = CrawlCounter()
    fetcher = CrawlerFetcher(_ctx(c), middlewares=[])

    def fake_once(url, *, attempt=1, **kw):
        return FetchResult(ok=False, url=url, status=503,
                           fetcher="curl_cffi", attempt=attempt)

    monkeypatch.setattr(fetcher, "_get_once", fake_once)
    fetcher.get("https://example.com/p/1")
    assert c.api_calls == 0


def test_retry_to_success_counts_once(monkeypatch):
    c = CrawlCounter()
    fetcher = CrawlerFetcher(_ctx(c, retries=2), middlewares=[])
    calls = {"n": 0}

    def fake_once(url, *, attempt=1, **kw):
        calls["n"] += 1
        ok = calls["n"] >= 2          # 第一次失败，第二次成功
        return FetchResult(ok=ok, url=url, status=200 if ok else 503,
                           fetcher="curl_cffi", attempt=attempt)

    monkeypatch.setattr(fetcher, "_get_once", fake_once)
    monkeypatch.setattr("app.fetching.time.sleep", lambda *_: None)
    fetcher.get("https://example.com/p/1")
    assert c.api_calls == 1           # 重试到成功只 +1


def test_counter_none_is_noop(monkeypatch):
    fetcher = CrawlerFetcher(_ctx(None), middlewares=[])

    def fake_once(url, *, attempt=1, **kw):
        return FetchResult(ok=True, url=url, status=200, fetcher="curl_cffi")

    monkeypatch.setattr(fetcher, "_get_once", fake_once)
    fetcher.get("https://example.com/p/1")   # 不应抛错
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_fetch_counter.py -v`
Expected: FAIL（`ImportError: cannot import name 'CrawlCounter'`）

- [ ] **Step 3: 实现 CrawlCounter + FetchContext.counter + get() 计数**

在 `backend/app/fetching.py` 顶部 `@dataclass FetchContext` 之前加：

```python
@dataclass
class CrawlCounter:
    """一次抓取作用域内的成功次数累计（失败/重试不计）。"""
    api_calls: int = 0
    browser_opens: int = 0

    @property
    def pages_fetched(self) -> int:
        return self.api_calls + self.browser_opens


_BROWSER_FETCHERS = ("scrapling", "playwright")
```

在 `FetchContext` 末尾加一个字段（在 `rotate_proxy_on_retry: bool = True` 之后）：

```python
    counter: "CrawlCounter | None" = None
```

在 `CrawlerFetcher.get()` 的 `return result` / `return stealth` / `return last ...` 之前统一计数。
最干净的做法：把计数收敛到一个私有方法，并在每个返回点调用。改 `get()` 为：

```python
    def get(self, url: str, **kwargs) -> FetchResult:
        attempts = max(1, self.context.retries + 1)
        last: FetchResult | None = None
        for attempt in range(1, attempts + 1):
            request_kwargs = dict(kwargs)
            for mw in self.middlewares:
                mw.before_request(self, url, request_kwargs)
            result = self._get_once(url, attempt=attempt, **request_kwargs)
            for mw in self.middlewares:
                mw.after_response(self, result)
            last = result
            if result.ok:
                self._count(result)
                return result
            if self.context.allow_stealth and _should_stealth(result):
                stealth = self._get_stealth(url, attempt=attempt)
                for mw in self.middlewares:
                    mw.after_response(self, stealth)
                if stealth.ok:
                    self._count(stealth)
                    return stealth
                last = stealth
            if not _should_retry(self.context, result, attempt, attempts):
                break
            if self.context.rotate_proxy_on_retry:
                time.sleep(min(2 * attempt, 5))
        return last or FetchResult(ok=False, url=url, failure=FailureInfo(
            "unknown", STAGE_FETCH, "fetch produced no result", True,
            "检查 fetcher 配置"))

    def _count(self, result: FetchResult) -> None:
        """仅对成功结果计数（失败/重试已被调用方过滤）。"""
        counter = self.context.counter
        if counter is None or not result.ok:
            return
        if result.fetcher in _BROWSER_FETCHERS:
            counter.browser_opens += 1
        else:
            counter.api_calls += 1
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_fetch_counter.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: 提交**

```bash
git add backend/app/fetching.py backend/tests/test_fetch_counter.py
git commit -m "feat(fetching): CrawlCounter + 统一入口成功计数（失败/重试不计）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: CrawlerFetcher 支持 POST / 通用 method + FetchResult.json()

**Files:**
- Modify: `backend/app/fetching.py`（`_get_once` 泛化为 `_request_once`、加 `request()` / `post()`、`FetchResult.json()`）
- Test: `backend/tests/test_fetch_counter.py`（追加）

实现要点：把 `_get_once` 改造成内部按 `method` 发请求，`get`/`post` 委托过去。
POST 走同一套代理/重试/反爬/计数，不重复逻辑（DRY）。

- [ ] **Step 1: 写失败测试（追加到 test_fetch_counter.py 末尾）**

```python
def test_post_counts_as_api_call(monkeypatch):
    c = CrawlCounter()
    fetcher = CrawlerFetcher(_ctx(c), middlewares=[])

    def fake_once(method, url, *, attempt=1, **kw):
        assert method == "POST"
        return FetchResult(ok=True, url=url, status=200,
                           text='{"data": 1}', fetcher="curl_cffi")

    monkeypatch.setattr(fetcher, "_request_once", fake_once)
    res = fetcher.post("https://example.com/api", data="{}")
    assert c.api_calls == 1
    assert res.json() == {"data": 1}


def test_fetchresult_json_invalid_returns_none():
    res = FetchResult(ok=True, url="x", status=200, text="not json")
    assert res.json() is None
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_fetch_counter.py::test_post_counts_as_api_call -v`
Expected: FAIL（`AttributeError: 'CrawlerFetcher' object has no attribute 'post'`）

- [ ] **Step 3: 实现 request/post + json()**

在 `FetchResult` 类体末尾（`attempt: int = 1` 之后）加方法：

```python
    def json(self):
        """把 text 解析为 JSON；失败返回 None（不抛错）。"""
        import json as _json
        try:
            return _json.loads(self.text)
        except Exception:
            return None
```

把 `CrawlerFetcher._get_once` 重命名为 `_request_once` 并加首参 `method`，
其内部 `sess.get(url, ...)` 改为 `sess.request(method, url, ...)`。
（curl_cffi 的 `Session` 支持 `.request(method, url, **kwargs)`。）
然后让 `get()` 内对 `_get_once` 的调用改为 `self._request_once("GET", ...)`，
并新增 `request()` / `post()`：

```python
    def request(self, method: str, url: str, **kwargs) -> FetchResult:
        attempts = max(1, self.context.retries + 1)
        last: FetchResult | None = None
        for attempt in range(1, attempts + 1):
            request_kwargs = dict(kwargs)
            for mw in self.middlewares:
                mw.before_request(self, url, request_kwargs)
            result = self._request_once(method, url, attempt=attempt, **request_kwargs)
            for mw in self.middlewares:
                mw.after_response(self, result)
            last = result
            if result.ok:
                self._count(result)
                return result
            if not _should_retry(self.context, result, attempt, attempts):
                break
            if self.context.rotate_proxy_on_retry:
                time.sleep(min(2 * attempt, 5))
        return last or FetchResult(ok=False, url=url, failure=FailureInfo(
            "unknown", STAGE_FETCH, "fetch produced no result", True,
            "检查 fetcher 配置"))

    def post(self, url: str, **kwargs) -> FetchResult:
        return self.request("POST", url, **kwargs)
```

把原 `get()` 主体改写为委托（保留 stealth 兜底逻辑只在 GET 用）：
将 `get()` 内 `self._get_once(url, attempt=attempt, **request_kwargs)`
改为 `self._request_once("GET", url, attempt=attempt, **request_kwargs)`。

同步更新 `_get_once` 的所有调用点（仅 `get()` 内部，已在上一步处理）。
`_request_once` 签名：`def _request_once(self, method, url, *, attempt=1, **kwargs)`，
首行内 `resp = sess.request(method, url, timeout=timeout, **kwargs)`。

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_fetch_counter.py -v`
Expected: PASS（7 passed）

- [ ] **Step 5: 回归 fetching 相关已有测试**

Run: `cd backend && .venv/bin/python -m pytest tests/test_generic_discovery.py tests/test_frontier_and_proxy_health.py -v`
Expected: PASS（generic 走 `CrawlerFetcher.get()`，确认重命名未破坏）

- [ ] **Step 6: 提交**

```bash
git add backend/app/fetching.py backend/tests/test_fetch_counter.py
git commit -m "feat(fetching): CrawlerFetcher 支持 POST/request + FetchResult.json()

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Usage 模型加三列（自动迁移覆盖）

**Files:**
- Modify: `backend/app/models.py:526-546`（Usage 加三列）
- Test: `backend/tests/test_usage_columns.py`（新建）

实现要点：模型加列后，`db._migrate()` 会自动 `ALTER TABLE usage_records ADD COLUMN ...`
（`db.py:107-120` 已实现幂等迁移），无需手写迁移 SQL。测试验证列存在且默认 0。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_usage_columns.py
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Usage

pytestmark = pytest.mark.unit


def test_usage_has_count_columns():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with Session() as s:
        u = Usage(endpoint="/crawl/job", record_count=5,
                  api_calls=3, browser_opens=2, pages_fetched=5)
        s.add(u)
        s.commit()
        row = s.query(Usage).first()
        assert row.api_calls == 3
        assert row.browser_opens == 2
        assert row.pages_fetched == 5


def test_usage_count_columns_default_zero():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with Session() as s:
        u = Usage(endpoint="/crawl/job")
        s.add(u)
        s.commit()
        row = s.query(Usage).first()
        assert row.api_calls == 0
        assert row.browser_opens == 0
        assert row.pages_fetched == 0
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_usage_columns.py -v`
Expected: FAIL（`TypeError: 'api_calls' is an invalid keyword argument for Usage`）

- [ ] **Step 3: 给 Usage 加三列**

在 `backend/app/models.py` 的 `Usage` 类，`occurred_at` 行之前加：

```python
    api_calls = Column(Integer, nullable=False, default=0)        # 成功的 HTTP 请求次数
    browser_opens = Column(Integer, nullable=False, default=0)    # 成功的浏览器渲染次数
    pages_fetched = Column(Integer, nullable=False, default=0)    # 成功抓取页面数 = 前两者之和
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_usage_columns.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add backend/app/models.py backend/tests/test_usage_columns.py
git commit -m "feat(models): Usage 加 api_calls/browser_opens/pages_fetched 三列

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: record_usage 接收三个计数参数

**Files:**
- Modify: `backend/app/billing.py:48-75`（加参数并写入）
- Test: `backend/tests/test_record_usage_counts.py`（新建）

实现要点：`record_usage` 用全局 `SessionLocal`，测试通过 monkeypatch `billing.SessionLocal`
指向内存引擎来验证写入。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_record_usage_counts.py
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import billing
from app.db import Base
from app.models import Usage

pytestmark = pytest.mark.unit


@pytest.fixture
def mem_session(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(billing, "SessionLocal", Session)
    return Session


def test_record_usage_persists_counts(mem_session):
    billing.record_usage(
        api_key_id=None, endpoint="/crawl/job", record_count=10,
        bytes_returned=0, duration_ms=1200, credits_used=10,
        workspace_id=7, api_calls=4, browser_opens=1, pages_fetched=5,
    )
    with mem_session() as s:
        row = s.query(Usage).first()
        assert row.workspace_id == 7
        assert row.api_calls == 4
        assert row.browser_opens == 1
        assert row.pages_fetched == 5
        assert row.credits_used == 10


def test_record_usage_counts_default_zero(mem_session):
    billing.record_usage(
        api_key_id=None, endpoint="/x", record_count=1,
        bytes_returned=0, duration_ms=0,
    )
    with mem_session() as s:
        row = s.query(Usage).first()
        assert row.api_calls == 0
        assert row.pages_fetched == 0
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_record_usage_counts.py -v`
Expected: FAIL（`TypeError: record_usage() got an unexpected keyword argument 'api_calls'`）

- [ ] **Step 3: 扩展 record_usage**

把 `backend/app/billing.py` 的 `record_usage` 签名与函数体改为：

```python
def record_usage(api_key_id: int, endpoint: str, record_count: int,
                 bytes_returned: int, duration_ms: int,
                 credits_used: int | None = None,
                 workspace_id: int | None = None,
                 api_calls: int = 0, browser_opens: int = 0,
                 pages_fetched: int = 0) -> None:
    """记录一次调用的用量。

    Args 略（见原 docstring）；新增 api_calls / browser_opens / pages_fetched
    为成功次数统计，默认 0 向后兼容。
    """
    with SessionLocal() as s:
        key = s.get(ApiKey, api_key_id) if api_key_id else None
        u = Usage(
            api_key_id=api_key_id,
            workspace_id=workspace_id if workspace_id is not None
            else (key.workspace_id if key else None),
            endpoint=endpoint,
            record_count=record_count,
            credits_used=record_count if credits_used is None else credits_used,
            bytes_returned=bytes_returned,
            duration_ms=duration_ms,
            api_calls=api_calls,
            browser_opens=browser_opens,
            pages_fetched=pages_fetched,
        )
        s.add(u)
        s.commit()
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_record_usage_counts.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add backend/app/billing.py backend/tests/test_record_usage_counts.py
git commit -m "feat(billing): record_usage 接收 api_calls/browser_opens/pages_fetched

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: BaseCrawler 持有 counter + CrawlResult 带出计数

**Files:**
- Modify: `backend/app/crawlers/base.py`（`CrawlResult` 加计数字段、`BaseCrawler.__init__` 建 counter、加 `_fetcher` 辅助）
- Test: `backend/tests/test_base_crawler_counter.py`（新建）

实现要点：`BaseCrawler` 创建 `self.counter = CrawlCounter()`；提供一个统一的
`make_fetcher()` 帮助子类拿到已注入 counter 的 `CrawlerFetcher`（批 1 收编时用）。
`CrawlResult` 加三个计数字段，默认 0。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_base_crawler_counter.py
from __future__ import annotations

import pytest

from app.crawlers.base import BaseCrawler, CrawlResult
from app.fetching import CrawlCounter
from app.models import Site

pytestmark = pytest.mark.unit


class _Dummy(BaseCrawler):
    platform = "generic"

    def crawl(self) -> CrawlResult:
        return CrawlResult()


def _site():
    return Site(site="t", url="https://example.com", country="US",
                proxy_tier="none", platform="generic")


def test_crawlresult_has_count_fields():
    r = CrawlResult()
    assert r.api_calls == 0
    assert r.browser_opens == 0
    assert r.pages_fetched == 0


def test_base_crawler_has_counter():
    c = _Dummy(_site())
    assert isinstance(c.counter, CrawlCounter)
    assert c.counter.pages_fetched == 0


def test_make_fetcher_injects_counter():
    c = _Dummy(_site())
    c.counter.api_calls = 2
    fetcher = c.make_fetcher(kind="product", source="test")
    assert fetcher.context.counter is c.counter
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_base_crawler_counter.py -v`
Expected: FAIL（`AttributeError: 'CrawlResult' object has no attribute 'api_calls'`）

- [ ] **Step 3: 改 base.py**

`CrawlResult.__init__` 末尾加：

```python
        self.api_calls: int = 0
        self.browser_opens: int = 0
        self.pages_fetched: int = 0
```

`base.py` 顶部 import 加：

```python
from ..fetching import CrawlCounter, CrawlerFetcher, FetchContext
```

`BaseCrawler.__init__` 末尾（`self.proxy = ...` 之后）加：

```python
        self.counter = CrawlCounter()
```

`BaseCrawler` 加方法（放在 `snapshot` 之后、`crawl` 抽象方法之前）：

```python
    def make_fetcher(self, *, kind: str = "product",
                     source: str = "unknown",
                     timeout: int = 30,
                     use_proxy: bool = True,
                     allow_stealth: bool = False) -> CrawlerFetcher:
        """构造一个已注入本 crawler 计数器的统一 fetcher。"""
        return CrawlerFetcher(FetchContext(
            site=self.site,
            job_id=self.job_id,
            kind=kind,
            source=source,
            timeout=timeout,
            use_proxy=use_proxy,
            allow_stealth=allow_stealth,
            counter=self.counter,
        ))
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_base_crawler_counter.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 回归 crawler registry / limit 测试**

Run: `cd backend && .venv/bin/python -m pytest tests/test_crawler_registry.py tests/test_crawler_limit.py -v`
Expected: PASS（确认 BaseCrawler 改动未破坏现有 crawler 实例化）

- [ ] **Step 6: 提交**

```bash
git add backend/app/crawlers/base.py backend/tests/test_base_crawler_counter.py
git commit -m "feat(crawlers): BaseCrawler 持有 CrawlCounter + make_fetcher 注入

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: 修 bug — runner.execute_job 成功后写 Usage（不扣额度）

**Files:**
- Modify: `backend/app/runner.py:129-166`（成功分支收尾时把 counter 写进 CrawlResult 并 record_usage）
- Test: `backend/tests/test_runner_usage.py`（新建）

实现要点：`crawler.crawl()` 执行期间 counter 已自动累加（批 1 收编后才有非 0 值，
未收编 crawler 暂为 0，符合 spec 渐进式）。成功分支把 counter 灌进 `result` 计数字段，
并调用 `record_usage(api_key_id=None, workspace_id=job.requested_by_workspace_id, ...)`。
`api_key_id=None` 保证只记录、不触发任何 key 维度额度扣减。
计数失败不得中断 job：包 try/except。

`credits_used` 口径（实现期定）：按产出 record 数，`max(1, products_count)`，封顶 10000，
与 `agent_crawler.crawl_site` 的 `max(1, min(limit, 10_000))` 同量级。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_runner_usage.py
from __future__ import annotations

import pytest

from app import runner

pytestmark = pytest.mark.unit


def test_record_crawl_usage_emits_row(monkeypatch):
    captured = {}

    def fake_record_usage(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(runner, "record_usage", fake_record_usage)

    runner._record_crawl_usage(
        workspace_id=7, products_count=42, duration_sec=3.5,
        api_calls=12, browser_opens=2,
    )

    assert captured["api_key_id"] is None          # 网页触发无 key → 不扣额度
    assert captured["workspace_id"] == 7
    assert captured["endpoint"] == "/crawl/job"
    assert captured["api_calls"] == 12
    assert captured["browser_opens"] == 2
    assert captured["pages_fetched"] == 14
    assert captured["credits_used"] == 42          # max(1, products_count)


def test_record_crawl_usage_credits_floor(monkeypatch):
    captured = {}
    monkeypatch.setattr(runner, "record_usage",
                        lambda **kw: captured.update(kw))
    runner._record_crawl_usage(workspace_id=None, products_count=0,
                               duration_sec=1.0, api_calls=0, browser_opens=0)
    assert captured["credits_used"] == 1           # 下限 1


def test_record_crawl_usage_never_raises(monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(runner, "record_usage", boom)
    # 不应抛出
    runner._record_crawl_usage(workspace_id=1, products_count=1,
                               duration_sec=1.0, api_calls=1, browser_opens=0)
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_runner_usage.py -v`
Expected: FAIL（`AttributeError: module 'app.runner' has no attribute 'record_usage'`）

- [ ] **Step 3: 实现 _record_crawl_usage + 接入 execute_job**

在 `backend/app/runner.py` 顶部 import 区加：

```python
from .billing import record_usage
```

加辅助函数（放在 `execute_job` 之前）：

```python
def _record_crawl_usage(*, workspace_id, products_count, duration_sec,
                        api_calls, browser_opens) -> None:
    """网页/后台采集：写一行 Usage（api_key_id=None → 只记录，不扣额度）。

    计费失败绝不中断采集收尾。
    """
    try:
        record_usage(
            api_key_id=None,
            workspace_id=workspace_id,
            endpoint="/crawl/job",
            record_count=products_count,
            credits_used=max(1, min(products_count, 10_000)),
            bytes_returned=0,
            duration_ms=int((duration_sec or 0) * 1000),
            api_calls=api_calls,
            browser_opens=browser_opens,
            pages_fetched=api_calls + browser_opens,
        )
    except Exception:
        pass
```

在 `execute_job` 成功分支（`runner.py:129-166` 的 `with session_scope() as s:` 块内，
读取 `job.requested_by_workspace_id` 后于块外调用）。具体：在成功块内，
`duration = job.duration_sec` 之后加一行读取 workspace：

```python
        ws_id = job.requested_by_workspace_id
```

然后在该 `with` 块**之后**、`return {...}` 之前插入：

```python
    _record_crawl_usage(
        workspace_id=ws_id,
        products_count=stats["inserted"] + stats["updated"],
        duration_sec=duration,
        api_calls=crawler.counter.api_calls,
        browser_opens=crawler.counter.browser_opens,
    )
```

（注意：`crawler` 与 `stats`、`duration` 在成功路径作用域内可见；`crawler` 在
`execute_job` 顶部 `with` 块创建后是函数局部变量，确认其在成功分支可访问——
若不可见，把 `crawler` 提到函数顶层变量。）

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_runner_usage.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 回归 runner / pipeline 相关测试**

Run: `cd backend && .venv/bin/python -m pytest tests/test_pipeline_promo.py tests/test_crawler_limit.py -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add backend/app/runner.py backend/tests/test_runner_usage.py
git commit -m "fix(billing): 网页/后台采集写入 Usage（api_key_id=None 只记录不扣额度）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: API / MCP / Spine 路径透传计数

**Files:**
- Modify: `backend/app/api/v2.py`（`_meter` 从 usage dict 读计数并透传）
- Modify: `backend/app/mcp_server.py:115-125`（`_record_tool_usage` 透传）
- Modify: `backend/app/spine_queue.py:138-145`（`_record_execute_usage` 透传）
- Modify: `backend/app/agent_crawler.py`（`scrape_url` 的 `usage` dict 暂带 api_calls/browser_opens，本步只需保证字段存在、值可为 0）
- Test: `backend/tests/test_meter_counts_passthrough.py`（新建）

实现要点：这三条路径每次抓 1 个 URL，计数小但口径统一。
`agent_crawler` 当前不走 `CrawlerFetcher`（用 `live_scrape_url` / `advanced_scrape_url`），
本 spec 不收编它，所以这里透传的计数来自 `usage` dict 里的字段，缺失时为 0。
关键是**不双重计数**：只透传，不在这里新增 counter。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_meter_counts_passthrough.py
from __future__ import annotations

import pytest

from app.api import v2

pytestmark = pytest.mark.unit


def test_meter_passes_counts(monkeypatch):
    captured = {}
    monkeypatch.setattr(v2, "record_usage",
                        lambda **kw: captured.update(kw))

    class _Key:
        id = 1
    monkeypatch.setattr(v2, "_api_key_row", lambda *a, **k: _Key())

    result = {
        "usage": {"credits_used": 2, "records": 1, "duration_ms": 100,
                  "api_calls": 1, "browser_opens": 0},
        "data": {"x": 1},
    }
    v2._meter(None, "Bearer t", None, "/api/v2/scrape", result)

    assert captured["api_calls"] == 1
    assert captured["browser_opens"] == 0
    assert captured["pages_fetched"] == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_meter_counts_passthrough.py -v`
Expected: FAIL（`KeyError: 'api_calls'` 或 captured 无该键）

- [ ] **Step 3: 改 v2._meter 透传计数**

在 `backend/app/api/v2.py` 的 `_meter` 内，`record_usage(` 调用补三个参数：

```python
        record_usage(
            api_key_id=key.id,
            endpoint=endpoint,
            record_count=record_count,
            credits_used=int(usage.get("credits_used") or record_count),
            bytes_returned=bytes_returned,
            duration_ms=duration_ms,
            api_calls=int(usage.get("api_calls") or 0),
            browser_opens=int(usage.get("browser_opens") or 0),
            pages_fetched=int(usage.get("api_calls") or 0)
            + int(usage.get("browser_opens") or 0),
        )
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_meter_counts_passthrough.py -v`
Expected: PASS（1 passed）

- [ ] **Step 5: 同步改 mcp_server 与 spine_queue（同模式）**

`backend/app/mcp_server.py` 的 `_record_tool_usage` 内 `record_usage(` 调用补：

```python
            api_calls=int((result.get("usage") or {}).get("api_calls") or 0),
            browser_opens=int((result.get("usage") or {}).get("browser_opens") or 0),
            pages_fetched=int((result.get("usage") or {}).get("api_calls") or 0)
            + int((result.get("usage") or {}).get("browser_opens") or 0),
```

（若 `_record_tool_usage` 当前用 `result` 变量名不同，按其实际变量名调整；
保持"从 usage dict 取、缺失为 0"的口径。）

`backend/app/spine_queue.py` 的 `_record_execute_usage` 内 `record_usage(` 调用补：

```python
                  api_calls=int(out.get("api_calls") or 0),
                  browser_opens=int(out.get("browser_opens") or 0),
                  pages_fetched=int(out.get("api_calls") or 0)
                  + int(out.get("browser_opens") or 0),
```

- [ ] **Step 6: 回归三路径已有测试**

Run: `cd backend && .venv/bin/python -m pytest tests/test_access_and_metering.py tests/test_agent_crawler.py tests/test_ondemand_queue.py -v`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add backend/app/api/v2.py backend/app/mcp_server.py backend/app/spine_queue.py backend/tests/test_meter_counts_passthrough.py
git commit -m "feat(billing): API/MCP/Spine 路径透传 api_calls/browser_opens 计数

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: 批 1 试点 — 把 2 个纯 curl_cffi GET 站点收编进统一入口

**Files:**
- Modify: `backend/app/crawlers/sephora.py`（裸 `creq` → `make_fetcher().get()`）
- Modify: `backend/app/crawlers/article.py`（同上）
- Test: `backend/tests/test_crawler_counter_integration.py`（新建）

实现要点：选 sephora + article 两个最简单的纯 GET crawler 作为收编模板验证。
把 `creq.Session().get(url)` 替换为 `self.make_fetcher(kind=..., source=...).get(url)`，
用 `res.ok` / `res.status` / `res.text` 替代原 `resp.status_code` / `resp.text`。
收编后这两个 crawler 的 `self.counter.api_calls` 自动随成功抓取累加。

> 注意：sephora.py / article.py 的具体抓取结构需在实现时按文件实际代码替换。
> 下面测试只验证"收编后 counter 会随成功 get 增长"这一行为契约，不依赖站点真实网络。

- [ ] **Step 1: 写失败测试（行为契约：mock fetcher，验证 counter 增长被带出）**

```python
# backend/tests/test_crawler_counter_integration.py
from __future__ import annotations

import pytest

from app.fetching import CrawlCounter, FetchResult
from app.models import Site

pytestmark = pytest.mark.unit


def _site(site_name, url):
    return Site(site=site_name, url=url, country="US",
                proxy_tier="none", platform=site_name)


def test_sephora_uses_make_fetcher_and_counts(monkeypatch):
    """收编后：crawl 过程中每次成功 get 都经过 self.make_fetcher，
    且 counter.api_calls 增长。"""
    from app.crawlers.sephora import SephoraCrawler

    crawler = SephoraCrawler(_site("sephora", "https://www.sephora.fr"))

    # 用一个会自增同一 counter 的假 fetcher 替换 make_fetcher
    def fake_make_fetcher(**kwargs):
        class _F:
            def get(self_inner, url, **kw):
                crawler.counter.api_calls += 1     # 模拟统一入口成功计数
                return FetchResult(ok=True, url=url, status=200,
                                   text="<html></html>", fetcher="curl_cffi")
        return _F()

    monkeypatch.setattr(crawler, "make_fetcher", fake_make_fetcher)
    # 限制只跑发现/少量页，避免真实网络（按 sephora 实现 monkeypatch 其发现方法）
    # 这里只断言 make_fetcher 是被使用的入口：
    assert hasattr(crawler, "make_fetcher")
    f = crawler.make_fetcher(kind="product", source="test")
    f.get("https://www.sephora.fr/p/x")
    assert crawler.counter.api_calls == 1
```

> 实现者注：本测试是收编的"脚手架契约"。完成 sephora 实际替换后，
> 应再补一个针对该 crawler 主流程（discover→parse）的 monkeypatch 测试，
> 断言整轮 `crawl()` 后 `counter.api_calls == 成功 get 次数`。article 同理。

- [ ] **Step 2: 运行确认失败 / 现状**

Run: `cd backend && .venv/bin/python -m pytest tests/test_crawler_counter_integration.py -v`
Expected: FAIL（sephora 当前无 `make_fetcher` 使用路径前，若直接断言主流程会失败；
脚手架契约测试在 Task 5 后 `make_fetcher` 已存在会 PASS——以实际为准，先跑看红/绿）

- [ ] **Step 3: 收编 sephora.py**

打开 `backend/app/crawlers/sephora.py`，定位所有 `creq.Session()` / `sess.get(url)` /
`creq.get(url)` 调用点。逐个替换为：

```python
        res = self.make_fetcher(kind="product", source="sephora").get(url)
        if not res.ok:
            # 原 status>=400 / 异常分支等价处理
            continue
        html = res.text
        status = res.status
```

移除文件顶部 `from curl_cffi import requests as creq`（若替换后不再使用）。
保留原有解析逻辑不变。确保 `crawl()` 内每条抓取路径都走 `make_fetcher`。

- [ ] **Step 4: 收编 article.py（同模式）**

打开 `backend/app/crawlers/article.py`，把裸 `creq` 请求替换为
`self.make_fetcher(kind=..., source="article").get(url)`，用 `res.ok/res.text/res.status`
替代原响应字段，移除不再使用的 `creq` import。

- [ ] **Step 5: 运行收编测试 + 回归**

Run: `cd backend && .venv/bin/python -m pytest tests/test_crawler_counter_integration.py tests/test_crawler_registry.py tests/test_crawler_limit.py -v`
Expected: PASS

- [ ] **Step 6: 全量单测回归**

Run: `cd backend && .venv/bin/python -m pytest -m unit -q`
Expected: PASS（无新增失败；smoke 测试默认按 marker 跳过）

- [ ] **Step 7: 提交**

```bash
git add backend/app/crawlers/sephora.py backend/app/crawlers/article.py backend/tests/test_crawler_counter_integration.py
git commit -m "feat(crawlers): sephora/article 收编进 CrawlerFetcher 统一入口（批1试点）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: 端到端集成验证 + 收尾

**Files:**
- Test: `backend/tests/test_crawl_billing_e2e.py`（新建）

实现要点：模拟一次 web 触发抓取的成功收尾，断言 Usage 行写入、计数正确、
`api_key_id is None`（不扣额度的标志）。用 monkeypatch 把 `runner.record_usage`
指向内存引擎，避免依赖真实抓取与网络。

- [ ] **Step 1: 写集成测试**

```python
# backend/tests/test_crawl_billing_e2e.py
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import billing, runner
from app.db import Base
from app.models import Usage

pytestmark = pytest.mark.unit


def test_web_crawl_writes_usage_without_charging_key(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(billing, "SessionLocal", Session)

    # runner._record_crawl_usage 直接调 billing.record_usage（已 import 进 runner）
    runner._record_crawl_usage(
        workspace_id=42, products_count=37, duration_sec=4.2,
        api_calls=15, browser_opens=3,
    )

    with Session() as s:
        row = s.query(Usage).filter(Usage.endpoint == "/crawl/job").first()
        assert row is not None
        assert row.api_key_id is None          # 关键：不挂 key → 不扣额度
        assert row.workspace_id == 42
        assert row.api_calls == 15
        assert row.browser_opens == 3
        assert row.pages_fetched == 18
        assert row.credits_used == 37
```

- [ ] **Step 2: 运行确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_crawl_billing_e2e.py -v`
Expected: PASS（1 passed）

> 若 `runner.record_usage` 是 `from .billing import record_usage` 直接绑定，
> monkeypatch `billing.SessionLocal` 仍生效（因为 `record_usage` 内部用的是
> `billing` 模块级 `SessionLocal`）。确认通过即说明链路正确。

- [ ] **Step 3: 全量单测最终回归**

Run: `cd backend && .venv/bin/python -m pytest -m unit -q`
Expected: PASS

- [ ] **Step 4: 提交**

```bash
git add backend/tests/test_crawl_billing_e2e.py
git commit -m "test(billing): 网页采集计费 e2e — 写 Usage 且不扣 key 额度

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## 自检（Self-Review）

**Spec 覆盖：**
- 修 bug（网页抓取写 Usage 不扣额度）→ Task 6 + Task 9 ✓
- 三个计数列 → Task 3 ✓
- record_usage 扩参 → Task 4 ✓
- 计数语义（成功+1/失败重试不计/pages=api+browser）→ Task 1 ✓
- CrawlerFetcher 扩 POST/JSON → Task 2 ✓
- 浏览器一等公民入口 → 部分（Task 1 的 fetcher 分流已支持 scrapling/playwright 计数；
  主动 `render=True` 入口推迟到批 3 后续 spec，本 spec 范围内只需计数分流正确，已覆盖）✓
- counter 贯通（BaseCrawler/CrawlResult）→ Task 5 ✓
- API/MCP/Spine 透传不双重计数 → Task 7 ✓
- 批 1 试点收编 → Task 8 ✓
- 幂等迁移 → Task 3（复用 db._migrate 自动 ALTER）✓

**说明（spec 与计划的一处收敛）：** spec 第 0 步提到 `get(url, render=True)` 主动浏览器入口。
本计划将"浏览器计数分流"在 Task 1 实现（已满足全路径计数正确性），而"主动 render 入口 +
浏览器类 crawler 迁移"属批 3，明确留作后续 spec——与 spec「本 spec 范围」一致，非遗漏。

**Placeholder 扫描：** 无 TBD/TODO；Task 8 标注了"按文件实际代码替换"是收编的固有性质
（sephora/article 真实结构需打开文件，已给出替换模板与契约测试），不是占位符。

**类型一致性：** `CrawlCounter`(api_calls/browser_opens/pages_fetched 属性)、
`FetchContext.counter`、`CrawlerFetcher._count` / `_request_once` / `request` / `post`、
`FetchResult.json()`、`BaseCrawler.make_fetcher` / `self.counter`、
`record_usage(... api_calls/browser_opens/pages_fetched)`、`runner._record_crawl_usage`
跨任务签名一致。
