# Spine 异步抓取队列 + 常驻 Worker · 设计文档

> **日期:** 2026-06-12
> **分支(待建):** `feat/spine-async-queue`
> **前置:** SP1 通用数据脊柱(已合并 main,`fbecf1e`)
> **状态:** 设计已确认,待写实现计划

## 目标

给 SP1 通用数据脊柱加一层异步抓取队列:任意 URL 入队 → 常驻 worker 消费 →
走 SP1 的 `resolve()`(warehouse-first + ingest)落库。让数据脊柱从"同步即抓即返"
变成"可异步、可重试、可常驻消费",而不碰现有电商采集路径,也不碰 SP1 同步入口。

## 核心原则

- **完全独立于电商队列**:新建 `spine_jobs` 表 + `spine_queue.py` + `spine_worker.py`,
  与现有 `crawl_jobs`/`runner.py`/`worker.py` 互不干扰(延续 SP1 spec「不碰现有电商路径」)。
- **复用 SP1 ingest 逻辑**:`execute_job` 调 `spine.resolve()`,队列消费与同步路径走
  同一套抓取/落库,行为一致,不重写。
- **退避不阻塞**:重试用 `next_attempt_at` 时间门控,claim 只领到期任务,worker 不 sleep 等退避。
- **现有行为零破坏**:同步 `POST /custom/scrape` 与 MCP `crawl_custom_source` 原样保留。

## 架构总览

```
入队侧                          队列(spine_jobs 表)            消费侧
─────                          ──────────────────            ─────
POST /custom/scrape/async  ─┐                            ┌─ spine_worker (常驻进程)
MCP enqueue_custom_scrape  ─┼─→ enqueue() → [pending] ──→ │   loop:
内部 enqueue()              ─┘                            │     claim_job()  ← 乐观锁
                                                          │     execute_job() → spine.resolve()
GET /custom/job/{id}  ←──────── 查状态                     │     成功→success / 失败→重试或failed
```

## 1. 数据模型 `SpineJob`(`models.py` 追加)

新表 `spine_jobs`。`SpineJob` 继承 `Base`,`init_db()` 里的
`Base.metadata.create_all(engine)` 会自动建表——**无需改 `db.py::_migrate()`**
(`_migrate` 只用于给已存在的表 ADD COLUMN)。

| 字段 | 类型 | 用途 |
|---|---|---|
| `id` | Integer PK | job_id |
| `url` | Text | 要抓的任意 URL |
| `dataset` | String | 落到哪个 dataset(SP1 dataset slug) |
| `entity_type` | String | product/review/article/generic |
| `save_policy` | String | promote_if_valid/main/staging(SP1 质量门) |
| `force_live` | Boolean | 是否跳过 warehouse-first |
| `status` | String, index | `pending`→`running`→`success`/`failed` |
| `retries` | Integer | 已重试次数,默认 0 |
| `max_retries` | Integer | 默认 3 |
| `next_attempt_at` | DateTime, index | 退避门控,claim 只领 `<= now` |
| `worker` | String | 领取的 worker 标识 |
| `result_record_id` | Integer nullable | 成功后指向 extracted_records.id |
| `error` | Text | 最后一次失败信息 |
| `workspace_id` | Integer FK | 多租户隔离(复用 SP1) |
| `created_at` | DateTime | 入队时间 |
| `started_at` | DateTime | 首次领取时间 |
| `finished_at` | DateTime | 终态时间 |

## 2. 队列模块 `app/spine_queue.py`(新建)

镜像 `runner.py` 的乐观锁模式(生产验证过)。

```
enqueue(db, url, dataset, *, entity_type="generic", save_policy="promote_if_valid",
        force_live=False, max_retries=3, workspace_id=None) -> int
    # 插一条 pending,next_attempt_at=now,返回 job_id

claim_job(worker_id) -> int | None
    # 原子领取:最旧的、status=pending 且 next_attempt_at<=now 的 job
    # 乐观锁 UPDATE ... WHERE id=? AND status='pending'(防多 worker 抢)
    # 领到置 running + started_at + worker

execute_job(job_id) -> dict
    # 1. 取 job,get_or_create_dataset(dataset, workspace_id, entity_type)
    # 2. spine.resolve(db, url, dataset, workspace_id=, force_live=, save_policy=)
    # 3. 成功 → status=success, result_record_id, finished_at
    # 4. 抛错 → _handle_failure()

_handle_failure(db, job, exc)
    # retries += 1
    # 若 retries < max_retries:
    #     status=pending, next_attempt_at = now + _backoff(retries)
    # 否则: status=failed, error=str(exc), finished_at=now

_backoff(retries) -> timedelta
    # 指数退避:1→30s, 2→2m, 3→10m
```

**关键点:**
- `execute_job` 复用 `spine.resolve()`,不重写抓取/落库。
- 退避用 `next_attempt_at`,不 sleep 阻塞 worker。
- `claim_job` 独立 session + 乐观锁,多 worker 安全(同 `runner.py:38`)。

## 3. 常驻 Worker `app/spine_worker.py`(新建)

```
run_loop(poll_interval=5)
    worker_id = f"spine-{hostname}-{pid}"
    while not _stop:
        job_id = claim_job(worker_id)
        if job_id is None:
            sleep(poll_interval)        # 空队列歇 5s
            continue
        try:
            execute_job(job_id)         # 失败已被 _handle_failure 内部兜住
        except Exception:
            log + continue              # worker 永不因单个 job 崩
```

- 入口 `python -m app.spine_worker`(镜像 `app/worker.py`)。
- `SIGTERM`/`SIGINT` → 设 `_stop`,领完当前 job 再退(优雅退出)。
- 单 job 异常被 catch,worker 进程不挂。
- 空队列 poll 5s,不空转。

## 4. systemd unit `deploy/spine-worker.service`(新建)

```ini
[Service]
ExecStart=/path/.venv/bin/python -m app.spine_worker
WorkingDirectory=/path/backend
Restart=always
```

生产 `systemctl enable --now smart-crawler-spine-worker`。
(NAS deploy memory 已记 worker.service 部署步骤。)

## 5. 对外入口

### REST(`api/v2.py`,镜像现有 `/custom/scrape`)

```
POST /api/v2/custom/scrape/async
    body: {url, dataset, entity_type?, save_policy?, force_live?, max_retries?}
    → {job_id, status: "pending"}

GET  /api/v2/custom/job/{job_id}
    → {job_id, status, retries, result_record_id?, error?, created_at, finished_at?}
```

### MCP(`mcp_server.py`,镜像现有 `crawl_custom_source`)

```
enqueue_custom_scrape(url, dataset, ...) → {job_id, status}
get_custom_job(job_id) → {status, ...}
```

两者复用现有鉴权(`_require_scope` / `metered_tool`)和 `workspace_id` 注入。
同步 `/custom/scrape` 原样保留。

## 6. 测试策略(全程 TDD)

| 层 | 测试 |
|---|---|
| 队列单元 | `enqueue` 落 pending;`claim_job` 乐观锁(两次 claim 同 job 只成功一次);`execute_job` 成功路径(mock `_do_scrape`)→ success + result_record_id;失败 → 重试 + `next_attempt_at` 退避;超 max_retries → failed;退避内 claim 不到 |
| Worker | `run_loop` 跑一轮消费一个 job;空队列 sleep;SIGTERM 优雅退出 |
| 端到端 | 入队(REST/MCP)→ 手动 `claim+execute` → job=success → `query_dataset` 查得到 → `GET job/{id}` 看状态 |

mock 边界:只 mock `spine._do_scrape`(网络),其余真实走库(同 SP1 测试)。

## 范围边界(YAGNI)

**这期做:** `spine_jobs` 表 + `enqueue/claim_job/execute_job` + 重试3次指数退避 +
`spine_worker` 常驻 + systemd unit + 异步 REST/MCP 入口 + job 状态查询 + 全程 TDD。

**这期明确不做:**
- ❌ 并发多 worker 调优 / 限流(乐观锁已防抢,不做压测)
- ❌ 任务优先级排序(先 FIFO,优先级留给后续热度调度)
- ❌ 站点冷却 / 反封禁熔断(留给反爬专项)
- ❌ Webhook 完成回调 / 进度推送
- ❌ 后台管理 UI(单独一期)

## 文件清单

| 文件 | 职责 | 新建/改 |
|---|---|---|
| `backend/app/models.py` | SpineJob 模型 | 改(追加) |
| `backend/app/spine_queue.py` | enqueue/claim/execute/退避 | 新建 |
| `backend/app/spine_worker.py` | 常驻消费 loop + 信号处理 | 新建 |
| `backend/app/api/v2.py` | async 端点 + job 状态查询 | 改(追加) |
| `backend/app/mcp_server.py` | enqueue_custom_scrape + get_custom_job | 改(追加) |
| `deploy/spine-worker.service` | systemd unit | 新建 |
| `backend/tests/test_spine_queue.py` | 队列 + worker 单测 | 新建 |
| `backend/tests/test_spine_queue_api.py` | REST/MCP 端到端 | 新建 |
