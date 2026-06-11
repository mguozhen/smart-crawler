# 标杆网站维护面板（Tracking Maintenance Panel）设计

- 日期：2026-06-11
- 状态：设计已审，待写实现计划
- 范围：验收报告"标杆网站维护面板"模块（D 档）整块从零实现

## Context（为什么做）

验收报告"标杆网站维护面板"15 项检查全 ×——两端皆无、后端 grep `tracking/benchmark` 零命中，是唯一"真缺功能"的整块。现状：`Site` 靠 `sites.yaml` 预配、按 `platform` 路由 crawler，**没有任何建/删/改站点的 API**。本设计补齐：一个 admin 可增删改、贴 URL 自动探测平台并触发抓取的站点追踪面板，复用现有 crawler / Trend / report 全链路。

## 关键决策（已与用户确认）

- **数据模型**：扩展现有 `Site` 表，不另建 `Tracking` 表（复用 Site→crawler→Product/Trend→report 全链路，products/30天销量/收入直接来自现有 `site_overview` 逻辑）。
- **Add Tracking**：贴 URL → 探测平台 → 建 Site + 加入 workspace + 立即 `enqueue` 触发一次抓取。
- **平台探测**：只覆盖 Shopify / generic-sitemap 两类，探不到则报 400（不硬塞 generic）。
- **权限**：admin / owner / super_admin 可增删改/暂停；其他登录用户只读。后端 `_require_admin` 兜底，不靠前端隐藏当安全。
- **Competitive Analysis 按钮**：验收标注"暂时隐藏"，本轮不做。

## 能力边界（诚实声明）

平台探测只覆盖 Shopify(`/products.json`) / 通用 sitemap 两类。现有定制 crawler（vidaxl/costway/bol/cdiscount 等）按已知站点硬编码，无法从任意 URL 反推。所以面板新增的站，能抓的主要是 Shopify 系独立站 + 有 sitemap 的站；强反爬或定制结构的站会落 `error` 状态。这是能力边界，非 bug。

## §1 数据模型改动

给 `Site` 表加 6 列（全部走 `db.py::_migrate()` 幂等 `ALTER TABLE ADD COLUMN`，SQLite/PG 兼容，无需手写迁移）：

| 列 | 类型 | 说明 |
|---|---|---|
| `track_status` | String, default `"tracking"` | `tracking` / `paused` / `error`（抓取异常置 error） |
| `source` | String, default `"yaml"` | `yaml`(种子) / `user`(面板建)。删除只允许 source=user |
| `creator` | String | 创建人 username |
| `review_rate` | Float | 留评率（Edit 可改，影响销量估算） |
| `created_at` | DateTime, default utcnow | 创建时间（排序：最新在前） |
| `updated_at` | DateTime | 最后编辑/抓取时间 |

**不入表、实时算**：Products(不含变体，= distinct `spu` 计数) / 30-Day Sales / 30-Day Revenue / Updated Time 复用现有 `site_overview` 的查询逻辑，避免冗余与不一致。Market(国旗) 用现有 `country` 字段前端映射。

种子站点迁移后 `source` 默认 `yaml`、`track_status` 默认 `tracking`、`created_at` 为迁移时间（可接受，排序仅影响展示）。

## §2 平台探测

新建 `backend/app/crawlers/detect.py`：`detect_platform(url) -> tuple[str | None, str]`，返回 `(platform, normalized_base)`。按成功率/成本顺序探测（`curl_cffi`，超时 8s，失败只返回 None 不抛）：

1. **Shopify** — GET `{base}/products.json?limit=1`，合法 JSON 且含 `products` 键 → `"shopify"`
2. **通用 sitemap** — `{base}/sitemap.xml` 或 `/robots.txt` 声明 sitemap → `"generic"`
3. **都不中** → `(None, base)`，API 返 `400 无法识别平台，请联系技术人员手工配置`

> 不探 Magento：它没有 Shopify `/products.json` 那种稳定公开端点，靠页面指纹（`Mage.`/`/static/version`）误报率高、易被反爬挡，不如不做。需要时人工在 `sites.yaml` 配 `platform: magento`。

`normalized_base` = URL 的 scheme+host（验收要求"仅维护网址固定部分"，去 path/query）。URL 上限 150 字符、brand 上限 50 字符在 API 层校验。

`site` 主键（如 `songmics_us` 格式）生成规则：从 host 取主域名片段 + country 后缀（如 `examplebrand_us`），与已有 site 冲突则加数字后缀去重。

## §3 API 端点

新增一组 `/api/tracking*`（复用现有 `require_user` + workspace 作用域；写操作用 `_require_admin` 门控）：

| 方法 | 路径 | 权限 | 作用 |
|---|---|---|---|
| GET | `/api/tracking` | 登录(只读) | 列表：site/brand/country/track_status/creator/created_at/updated_at + 实时算 products(distinct spu)/30天销量/收入。筛选 `search`(URL/brand)、`market`、`brand`、`status`；`page/page_size`(10/20/50/100/200)；默认 `created_at` 倒序 |
| POST | `/api/tracking` | admin | Add：`{url, brand?, country?}` → `detect_platform` → 建 Site(source=user, creator=当前用户, track_status=tracking) + 加入当前 workspace(WorkspaceSite) + `enqueue` 触发一次抓取。探测失败返 400 |
| PATCH | `/api/tracking/{site}` | admin | Edit：改 brand / review_rate / country。URL、platform 不可改 |
| POST | `/api/tracking/{site}/pause` | admin | Stop Tracking：track_status→paused，定时调度跳过该站 |
| POST | `/api/tracking/{site}/resume` | admin | Start Tracking：track_status→tracking |
| DELETE | `/api/tracking/{site}` | admin | 删除：**仅 source=user**（种子站不可删），前端二次确认；连带解绑 WorkspaceSite |
| GET | `/api/tracking/export` | 登录 | 按当前筛选导出 .xlsx，表头同面板（复用 `export_workbook`） |

定时调度（`scheduler.py`）在选站时增加 `track_status != "paused"` 过滤，使 pause 生效。Action→Report 直接跳现有 `/report?site=`，无新端点。

**异常状态（error）置位规则**：站点一次抓取 job 结束后，若该 job `status=failed` 或抓到 product 数为 0，则把 Site.track_status 由 `tracking` 置 `error`（不覆盖用户手动设的 `paused`）；下次成功抓到 product 时复位回 `tracking`。在 `runner.py` 采集 job 收尾处实现（与现有 `last_crawled` 更新同一处）。

## §4 前端结构

新增 `frontend-app/src/pages/TrackingPage.vue` + `frontend-app/src/api/tracking.ts`，在 `app/router.ts` 和 `components/layout/AppLayout.vue` 导航注册 tab（"🎯 标杆维护"）。

- 表格列：Market(国旗) / Brand / URL / Status(Tracking/Paused/⚠️异常 三色 badge) / Products / 30-Day Sales / 30-Day Revenue / Updated / Created / Creator / Action(Report·Edit·Pause·Delete)
- 顶部：`+Add Tracking`（弹窗：URL 必填 + brand/country 选填）/ 搜索框 / Market·Brand·Status 三个筛选下拉 / 导出按钮
- 分页 10/20/50/100/200，默认 Created 倒序
- 复用本会话已建：`fmtPrice`(货币)、`.title-text`(URL 省略号)、`canEdit`(权限门控)、分页控件样式

## §5 权限

`canEdit = role admin/owner 或 global_role super_admin`（同本会话 SiteReportPage 的 `canEdit`）。后端写端点全部 `_require_admin` 兜底。非管理员：列表只读、增删改/暂停按钮不显示、后端 403。

## 验证

- 后端：`pytest` 绿 + 新增 tracking CRUD 测试（建站→列出→编辑→暂停→删除；探测 mock）。迁移演练：真实库副本跑 `init_db()` 两次幂等、数据零丢失、6 列加上。
- 探测：对已知 Shopify 站（songmics 类）、有 sitemap 的站、纯静态无 sitemap 站各跑一次 `detect_platform`，验证 Shopify/generic 两分支 + 400。
- 前端：`pnpm build` 过 vue-tsc；本地起服务，admin 登录走通 Add(探测→建站→触发)→列表→筛选→Edit→Pause(调度跳过)→Delete(仅 user 站)→导出；非 admin 只读。
- 端到端：Add 一个真实 Shopify 测试站，确认 enqueue 的 job 被 worker 执行、抓到 product、面板 Products 数实时反映、Action→Report 打开报表。

## 不在本轮范围

- Competitive Analysis 功能页（验收标注暂隐藏）
- 从任意 URL 反推定制 crawler（vidaxl/costway 等）
- 探测覆盖 Shopify/generic 之外的平台（含 Magento——需人工在 sites.yaml 配）
