# Aosen 全量需求 · 标杆数据 100% 覆盖

> 客户：宁波遨云科技有限公司（Aosen / 遨森）
> 签字方：陈茂杰（cmj@aosom.com）
> 合同：Shulex × 遨森 数据获取洞察项目（3 年，2024-10-28 → 2027-10-27）
> 当前阶段：Year 3 启动，客户主动反馈"数据太少"，进入合同执行强化期

## 1. 业务背景与触发事件

2026-05-27 客户群里反馈：截图显示 vidaxl.nl `/g/436/meubelen` 仅 furniture 一个大类就有 **150,754 Resultaten**，但 Shulex 后台 "应抓 SKU" 列只显示 **6,000**，"实际抓取 SKU" 5,000，覆盖率自我标记为 83%。这是 **校准口径与商业现实严重背离** 的暴露。

陈茂杰是合同甲方授权签字代表，**他的反馈即合同 SLA 事件**，按 Clause 9.3：交付内容与附件不符按合同总价 万分之五/天 计违约金，15 天宽限后视为根本违约。

## 2. 需求范围（甲方视角）

| 项 | 当前状态 | 目标 | 优先级 |
|---|---|---|---|
| **vidaxl 12 站全覆盖** | 17.4% URL coverage (471k / 2.72M) | ≥90% unique parent，含 SKU/价/库存/EAN/图/描述 | P0 |
| **应抓口径校准** | 已修：dashboard 改为读 sitemap 真实总数 | 客户可在 `/app` 看到诚实数字 | ✅ Done |
| **覆盖率推进**（每日 delta） | APScheduler 02:00 UTC 入队，但 worker 易死 | 24/7 稳定增量，可观测 | P0 |
| **其余 ~45 个标杆站** | 大部分 50-100% 覆盖（小站，sitemap 小） | 维持 + 增量监控 | P1 |
| **AI 评论打标** | 历史 + 增量打标已上线 | 维持 | P1 |
| **每日 delta 5 件套** | sitemap diff / top SKU / promo / review / aggregate | 维持 | P2 |

## 3. 工程现状（2026-05-28 21:00 UTC 快照）

```
vidaxl 实抓 SKU: 471,917
vidaxl sitemap URL 总: 2,717,274
URL 覆盖率: 17.37%
unique parent 覆盖率（估算 ~600k 真实独立产品）: ~78%
```

| 站点 | 实抓 | Sitemap URL | URL 覆盖 |
|---|---|---|---|
| vidaxl_ie | 66,300 | 165,125 | **40.15%** |
| vidaxl_uk | 44,560 | 151,009 | 29.51% |
| vidaxl_it | 60,925 | 299,133 | 20.37% |
| vidaxl_ro | 58,832 | 297,417 | 19.78% |
| vidaxl_nl | 56,333 | 304,024 | 18.53% |
| vidaxl_de | 52,617 | 303,701 | 17.33% |
| vidaxl_pt | 47,817 | 298,640 | 16.01% |
| vidaxl_pl | 42,260 | 299,421 | 14.11% |
| vidaxl_es | 25,315 | 297,537 | 8.51% |
| vidaxl_fr | 16,958 | 301,267 | 5.63% |
| vidaxl_ca | 0 | — | sitemap 空，业务暂停 |
| vidaxl_us | 0 | — | 401 Demandware Basic Auth 墙 |

## 4. 三条到达 100% 的路径

### 4.1 路径 A — 官方 Dropshipping API（推荐，48 小时 100%）

Vidaxl 提供 [B2B Dropshipper](https://b2b.vidaxl.com/) 账号注册（免费），后台生成 API token。

**优势**：
- 单一凭据通杀 12 站（含 us / ca）
- 字段比爬虫更全（B2B 价、零售建议价、库存、EAN/GTIN、变体）
- 合规、无反爬对抗、无代理费用

**所需输入**：
- `VIDAXL_API_EMAIL` — 注册邮箱
- `VIDAXL_API_TOKEN` — 后台生成

**代码就绪**：`backend/app/crawlers/vidaxl.py` 中 `_crawl_api()` 路径已实现，凭据到 env 即跑。

**预估时间**：拿到凭据后 48 小时全 12 站 100%。

**阻塞**：需客户 IT 配合 5 分钟注册一个 B2B 账号。**这是 P0 的真正路径**。

### 4.2 路径 B — 当前 storefront + 双源并行（已上线）

**架构**：
- NAS Docker (192.168.1.80)：2 worker thread × VIDAXL_CONCURRENCY=5 = 10 并发，走 11 个住宅代理池
- MacBook (192.168.1.91)：1 进程 × VIDAXL_CONCURRENCY=4 = 4 并发，走 MacBook 自家 IP 直连
- 数据写同一个 NAS PostgreSQL（端口 15432 暴露到 LAN）

**当前吞吐**：~30-40k 新 SKU / 小时

**预估时间到 100% URL 覆盖**：~75-100 小时（3-4 天连续跑），但实际触顶 600k unique parent 后 storefront 路径会进入 "高 fetch 低 new" 阶段，物理上限 ≈ unique parent 总数。

### 4.3 路径 C — 扩代理池 + 多机协同

- 加 50 IP 住宅代理（iMac 192.168.1.91 计划托管 3proxy，未上线）
- 预期吞吐拉到 100-150k/h
- 全 URL 覆盖压缩到 24-48h
- 工程量：3proxy 部署 + 代理供应商账号

## 5. 工程修复（本次 session 已交付）

| 根因 | 修复 | Commit |
|---|---|---|
| Dashboard 应抓 SKU 自我设限 6k | `_FULL_ESTIMATES` 改为读 sitemap 真实总数 (`/app/data/sitemap_totals.json`) | feature/customer-design-cards |
| 单 run 30 min 必超时 | `WORKER_JOB_TIMEOUT` 1800→5400s + `WORKER_THREADS` env | 同上 |
| Worker 守护线程 signal.alarm ValueError 静默死 | `_set_alarm` 包装 + try/except | 同上 |
| 每轮抓相同 URL（无 resume） | `_already_crawled_urls` 按 product_url dedup | 同上 |
| Sitemap variant 聚集（顺序切片每 run 仅 3k unique） | `random.shuffle(fresh)` 随机采样 | 同上 |
| URL-EAN ≠ JSON-LD-SKU 导致 SKU dedup 失效 | 注释说明 + 保留 URL dedup（正确路径） | 同上 |
| 1 worker = serial bottleneck | `WORKER_THREADS=2` + 多 worker 并发 | 同上 |
| MacBook 闲置 | 加 `macbook_vidaxl_worker.py` + PG 15432 端口暴露，本机协同写 NAS | 同上 |
| 单 run 失败率 80%（变体重复 + 静默丢弃） | `_fetch_one` 改为按状态码桶分类 + 重试 2 次 + stdout 诊断输出 | 同上 |

## 6. 验收口径与可交付物

### 6.1 客户可见证据

- `https://smartcrawler.io/app` — 实时 dashboard，应抓 vs 实抓 vs 覆盖率
- `https://smartcrawler.io/d/vidaxl_progress_2026-05-28.html` — 进度大盘
- `https://smartcrawler.io/d/vidaxl_live_progress.html` — 实时静态报告
- `https://smartcrawler.io/d/aosen_progress_message_2026-05-28.md` — 客户消息文档
- API：`/api/coverage` 实时覆盖率数据
- 数据导出：`/api/sites/{site}/products` 或 v2 API

### 6.2 阶段性 milestone

| Milestone | 衡量 | 触发动作 |
|---|---|---|
| M1 — 应抓口径校准 | dashboard 数字与 sitemap 实际一致 | ✅ Done 2026-05-28 |
| M2 — 单 run 不再 0 commit | 每个 vidaxl 完成 run 都新增 ≥3k SKU | ✅ Done 2026-05-28 |
| M3 — 50% URL coverage | 1.36M SKU 入库 | 路径 A: 凭据到手 +6h；路径 B: ~24h 持续跑 |
| M4 — 90% unique parent | ~540k SKU 入库 | 路径 A: 凭据到手 +12h；路径 B: 今晚-明上午 |
| M5 — 100% URL coverage | 2.72M URL 全抓 | 路径 A: 凭据到手 +48h；路径 B: ~75-100h |
| M6 — us + ca 覆盖 | 当前 0 → 全 | 仅路径 A 可达 |

### 6.3 失败 / 风险信号（持续监控）

- 单 run 失败率 > 30% → 触发 contention 调参（concurrency 降）
- 代理池 hot 数 > 5 → 触发代理轮换或暂停
- vidaxl 返回 403/429 占比 > 20% → 单站熔断 + 切代理
- 日 SKU 增量 < 50k → 调度健康度告警

## 7. 决策点（待用户拍板）

1. **路径选择**：
   - [ ] 路径 A（API 凭据，48h 完事，需客户协作）
   - [x] 路径 B（当前 storefront + 双源，3-4 天）—— 默认进行中
   - [ ] 路径 C（扩代理池，需 3proxy 部署 + 代理采购）

2. **vidaxl_us / vidaxl_ca**：
   - [ ] 接 API（路径 A 唯一可解）
   - [ ] 标记 "已知不支持站点" 按 43/46 站点交付
   - [ ] 加美国住宅代理 → 仅解 us（约 $50-200/月）

3. **附件《工作说明书》技术 SKU 覆盖率指标**：当前合同附件 PDF 未含此细则，建议与陈茂杰确认覆盖率口径是按 URL 还是 unique parent。

## 8. 联系人

- 项目接口：boyuan@solvea.cx
- 客户接口：cmj@aosom.com（陈茂杰，15381890508）
- 乙方 PM：杨琪科

---

文档维护：Shulex × Aosen 项目组
最后更新：2026-05-28
