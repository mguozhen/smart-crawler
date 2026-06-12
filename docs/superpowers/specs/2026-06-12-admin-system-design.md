# smart-crawler 后台管理系统(独立 admin-app) · 设计文档

> **日期:** 2026-06-12
> **分支(待建):** `feat/admin-system`
> **前置:** SP1 数据脊柱 + spine 异步队列 + 计费 + 心跳(均已合并 main)
> **状态:** 设计已确认,待写实现计划

## 目标

给 smart-crawler 建一个**独立的超管后台系统**:超管登录后可视化管理 spine 数据脊柱的全部运行面——队列、数据集、计费、健康、审计,以及复用现有的租户/用户管理。独立前端工程,不碰现有 `frontend-app`(业务控制台)。

## 范围:7 个模块(一期全做)

| 模块 | 内容 | 后端 |
|---|---|---|
| 1 概览仪表盘 | 关键指标卡 + 趋势 + 异常红点 | 聚合现有 stats,不新建端点 |
| 2 租户/用户管理 | workspace/user/invite/key CRUD + 配额 | 复用现有 `/api/admin/*` |
| 3 数据集管理 | datasets 列表、记录浏览、promote、删除 | 新建 |
| 4 队列监控 | spine_jobs 列表/详情/重试/入队/stats | 新建 |
| 5 计费用量 | Usage 按 key/租户/endpoint 聚合 | 新建 |
| 6 Worker/健康 | worker 状态(heartbeat 推断)/reclaim/存储/配置 | 新建 |
| 7 审计日志 | admin 写操作审计 | 新表 + 埋点 + 端点 |

## 整体架构

```
admin-app/(新独立 Vue3 工程,与 frontend-app 平级)
  Vite + Nuxt UI + Pinia + vue-router(参照 frontend-app 模式)
  构建 → admin-app/dist → 后端 FastAPI StaticFiles 挂载到 /admin

鉴权:超管 JWT 登录 → 前端 router guard 校验 global_role=="super_admin"
     → 所有 /api/admin/spine/* 后端 _require_super_admin 双重门

后端:新建 backend/app/api/admin_spine.py(router 前缀 /api/admin/spine)
     与现有 routes.py 的 /api/admin/* 并列,不碰现有 admin 端点
```

## 后端端点契约(`/api/admin/spine/*`,全部 `_require_super_admin`)

### 队列(模块4)
```
GET  /jobs        ?status=&dataset=&tenant=&page=&size=   → {items, total}
GET  /jobs/{id}                                            → 详情
POST /jobs/{id}/retry                                      → 失败 job 重置 pending(审计)
POST /jobs/enqueue   {url, dataset, entity_type?, save_policy?}  → job_id(审计)
GET  /jobs/stats                                          → {pending, running, success, failed, stuck}
```
- stuck = status=="running" 且 heartbeat_at < now-600s 的计数。

### 数据集(模块3)
```
GET    /datasets                                  → [{id, name, slug, entity_type, record_count, workspace_id}]
GET    /datasets/{id}/records  ?quality_status=&page=&size=  → {items, total}
GET    /records/{id}                              → {data, provenance, snapshot}
POST   /records/{id}/promote                      → quality_status→main(审计)
DELETE /records/{id}                              → 删除(审计)
```

### 计费(模块5)
```
GET /usage           ?start=&end=&endpoint=   → 聚合
GET /usage/by-key                              → 按 api_key 分组
GET /usage/by-tenant                           → 按 workspace 分组
```
- 异步抓取记在 endpoint `/spine/worker/execute`,与同步 `/api/v2/*` 分开展示。

### 健康/配置(模块6)
```
GET /health   → {worker_status, last_consumed_at, reclaim_count, snapshot_storage}
GET /config   → {ttl, heartbeat_interval, backoff_table}(只读)
```
- worker_status 用 heartbeat 推断:看 spine_jobs 最近 heartbeat_at / 最近 success 时间,在阈值内则 "running",否则 "idle/down"。

### 审计(模块7)
```
GET /audit    ?actor=&action=&start=&end=&page=  → {items, total}
```

### 概览(模块1)
前端聚合 `/jobs/stats` + `/usage` + `/health` + 现有 `/api/admin/users`,不新建端点。

## 审计(模块7)

新表 `admin_audit_logs`:
```
id / actor_user_id(FK users)/ actor_name / action(str)/ target_type(str)/
target_id(str)/ detail(JSON)/ ip(str)/ created_at(DateTime, index)
```
埋点(统一 helper `record_audit(db, actor, action, target_type, target_id, detail, ip)`):
- spine 写操作:`/jobs/{id}/retry`、`/jobs/enqueue`、`/records/{id}/promote`、`DELETE /records/{id}`
- 现有 admin 写操作(在 routes.py 现有端点补埋点):workspace/user/invite/key 的创建/修改/配额变更

## 前端工程结构(admin-app)

```
admin-app/
├─ package.json / vite.config.ts / tsconfig.json / index.html
└─ src/
   ├─ app/main.ts、router.ts(super_admin guard)
   ├─ stores/auth.ts(JWT + global_role 校验,参照 frontend-app)
   ├─ api/client.ts(apiJson,Bearer,401 处理)
   ├─ api/{queue,datasets,usage,health,audit,tenants}.ts
   ├─ components/layout/AdminLayout.vue(侧边导航 7 项)
   ├─ components/common/(StatCard / StatusBadge / DataTable / Pager)
   └─ pages/
      ├─ LoginPage.vue
      ├─ OverviewPage.vue       (模块1,轮询)
      ├─ TenantsPage.vue        (模块2,对接现有 /api/admin/*)
      ├─ DatasetsPage.vue + DatasetDetailPage.vue  (模块3)
      ├─ QueuePage.vue          (模块4,轮询)
      ├─ UsagePage.vue          (模块5)
      ├─ HealthPage.vue         (模块6)
      └─ AuditPage.vue          (模块7)
```

布局:侧边栏 7 项(概览/租户用户/数据集/队列/计费/健康/审计)。

## 刷新策略

- 默认手动刷新(每页一个刷新按钮)。
- 队列页 + 概览页加轮询:`setInterval` 5s,复用页面 load 函数,onUnmounted 清理,可开关(默认开)。后端不加任何东西。
- 不做 SSE/WebSocket。

## 数据流

超管登录 → JWT 存 localStorage → router guard 校验 global_role → 页面调 `/api/admin/spine/*` → 后端 `_require_super_admin` 双重门 → 查 spine 表/Usage/audit → 返回 → 表格/echarts 渲染。

## 测试策略

| 层 | 测什么 | 怎么测 |
|---|---|---|
| 后端端点 | 每个端点:鉴权门(非超管 401/403)+ 正确返回 | pytest + TestClient,造 super_admin JWT(参照现有 admin 测试) |
| 审计埋点 | 写操作后 admin_audit_logs 多一行(actor/action/target 对) | pytest |
| 前端 | `vite build` 通过 + 关键页面渲染冒烟 | 构建验证 +(可选)Playwright 冒烟 |

后端走完整 TDD;前端以 "build 通过 + 端点对接正确" 验收(参照 frontend-app 现状)。

## 范围边界(YAGNI)

**做:** 7 模块全部 + 审计表/埋点 + 独立工程脚手架 + 超管双重鉴权 + 队列/概览轮询。

**不做:** SSE/WebSocket 实时推送;审计回放/diff;记录批量编辑;花哨可视化(echarts 简单图表即可)。

## 文件清单

| 文件 | 职责 | 新建/改 |
|---|---|---|
| `backend/app/models.py` | AdminAuditLog 模型 | 改 |
| `backend/app/api/admin_spine.py` | spine admin 端点(队列/数据集/计费/健康/审计) | 新建 |
| `backend/app/audit.py` | record_audit helper | 新建 |
| `backend/app/api/routes.py` | 现有 admin 写操作补审计埋点 | 改 |
| `backend/app/main.py` | 挂载 admin_spine router + /admin StaticFiles | 改 |
| `admin-app/**` | 独立 Vue3 工程(脚手架 + 8 页面 + api + 布局) | 新建 |
| `backend/tests/test_admin_spine.py` | 端点鉴权 + 返回 + 审计埋点 | 新建 |

## 实现分解提示(供 writing-plans)

后端先行(每个端点组 TDD):审计表+helper → 队列端点 → 数据集端点 → 计费端点 → 健康端点 → 审计端点 → 现有 admin 埋点。
前端后行:工程脚手架(build 通过)→ auth+布局+client → 各模块页面逐个对接。

## 实现注意点(已核实)

- 现有 `main.py` 静态挂载用 `/assets`(frontend-app)+ `/mcp`,**`/admin` 无冲突**。
- admin-app 是 SPA 且挂在子路径 `/admin`,**Vite 必须配 `base: "/admin/"`**,否则构建产物里的 `assets/` 引用路径会指向根而 404。后端挂载需:① `/admin/assets` → admin-app/dist/assets;② `/admin` 其余路径回退到 admin-app/dist/index.html(SPA history 路由)。参照 frontend-app 现有挂载方式扩展。
- super_admin 鉴权:后端 `_is_super_admin`/`_require_super_admin` 已在 routes.py 存在,admin_spine.py 复用;不重新实现。
