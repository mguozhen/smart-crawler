# 限速 + 住宅 IP 兜底设计

日期：2026-06-17
状态：设计已确认，待实现

## 背景与问题

近 48 小时生产数据（NAS `smart-crawler-pg`，`crawl_failures` 表 75585 条）：

| 失败类型 | 数量 | 占比 |
|---|---|---|
| http_429 | 52966 | 70% |
| anti_bot_challenge | 21444 | 28% |
| 其余 | 1175 | 2% |

按站点拆 429，**6 个 costway magento 站贡献 52864 个 429，占全部 429 的 99.8%**：

| 站点 | platform | proxy_tier | 429 数 |
|---|---|---|---|
| costway_it | magento | none | 15082 |
| costway_uk | magento | none | 11299 |
| costway_es | magento | none | 8356 |
| costway_de | magento | none | 7098 |
| costway_fr | magento | none | 6180 |
| costway_nl | magento | none | 4849 |

### 根因（三因素叠乘）

1. **magento crawler 8 线程裸跑无限速**：`app/crawlers/magento.py:151`
   `ThreadPoolExecutor(max_workers=8)`，线程内 `_fetch_one`（`magento.py:163`）
   **没有 `self.sleep()`**——`antiban.py` 的 2s 限速档对它完全失效。
2. **`proxy_tier=none`，全走 NAS 单一出口 IP**：8 线程同一 IP 猛打。
3. **退避太软**：`app/fetching.py:179` `time.sleep(min(2*attempt, 5))`，5s 封顶，
   站点要求等更久时二次 429。

### 对照证据（关键）

同品牌 costway：`costway_us`/`costway_ca`（vue_spa，串行 + sleep）**429=0**；
`costway_pl`（shoper）**429=0**；只有 6 个 magento 站爆 429。
**同一出口 IP，唯一差别是发包速率。** 说明根因是速率超标，不是 IP 被指纹识别
（若是 IP 问题会大量 403/anti_bot，而非 429）。platform 分类经实测页面指纹确认无误
（命中 `Magento_*` / `data-mage-init` / `x-magento` 等）。

## 目标

- 把 429 压到接近 0（限速治本）。
- 直连撞墙时自动升级住宅 IP 兜底（省钱：只有真撞墙才用代理）。
- 做在统一 fetcher 层，所有走 `make_fetcher` 的爬虫自动受益，爬虫代码零改动。

## 非目标（YAGNI）

- **AIMD 动态降速**：固定速率 + 住宅兜底已覆盖 99.8% 的 429，不做。等上线后
  观察到"已限速仍持续 429"再加。
- **跨 worker 分布式限速**：magento 8 线程并发发生在单 job 内（单进程
  ThreadPoolExecutor），同一站点同时一般只有一个 running job，进程内令牌桶足够。
- anti_bot_challenge（28%）是另一类问题（需代理 + 浏览器），本设计不处理。

## 架构

三个组件全部落在统一 fetcher 层（`app/fetching.py` + `app/antiban.py`）：

```
CrawlerFetcher._retry_loop
   │
   ├─[1] RateLimiter.acquire(site)      ← 新增：发包前过令牌桶（8 线程在此排队）
   │
   ├──── _request_once() 真正发请求
   │
   └─[2] RetryMiddleware.after_response  ← 实现现在的空壳：
              429/503 → 读 Retry-After 或指数退避 → sleep
              单 job 累计 N 次 4xx/anti_bot → [3] 触发住宅升级
                                                ↓
                                   ProxyMiddleware 下次 before_request
                                   改用 tier=residential 取代理
```

### 升级作用域（核心默认）

升级状态 = 单个 `CrawlerFetcher` 实例 = 单个 job。一旦某 job 撞墙升级，该 job
剩余请求都走住宅代理；job 结束（fetcher 实例销毁）自动复位，下个 job 默认仍直连。
既省钱（只有真撞墙才用代理），又稳（不在直连/代理间反复横跳）。

## 三个组件

### 1. RateLimiter（令牌桶）

- 位置：`app/antiban.py`，模块级 `dict[site] -> bucket` + 锁。
- 速率复用现有 `RATE_TIERS`，语义从"sleep 间隔"变为"最小请求间隔"，数值不变。
- fetcher 每次发包前 `acquire(site)`；magento 8 个并发线程抢同一个桶 → 整站
  **合计** 0.5 req/s，而非现在的 8×无限。
- `acquire()` 设最大等待上限（默认 30s），极端情况不让线程无限阻塞。
- 桶状态随进程释放，无需持久化。

速率表（沿用 `RATE_TIERS`，单位秒 = 最小请求间隔）：

| platform | 间隔 | ≈ 速率 |
|---|---|---|
| magento | 2.0 | 0.5 req/s |
| generic / nuxt / vidaxl | 2.0~2.5 | ~0.4 req/s |
| shopify | 1.0 | 1 req/s |
| shoper | 0.35 → **1.0**（提到合理值） | |
| 未配置默认 | 1.5 | 0.67 req/s |

### 2. 退避（实现 RetryMiddleware）

替换 `app/fetching.py:179` 现有的 `time.sleep(min(2*attempt, 5))`：

- 有 `Retry-After` 头 → 按它 sleep，**封顶 60s**（防恶意大值）。
- 无头 → `min(2.0 × 2^(attempt-1) + random(0~1), 60)`，即 2→4→8→16…→60s 封顶。

逻辑落在现在空壳的 `RetryMiddleware`（`app/fetching.py:388`），而非散在
`_retry_loop` 里。

### 3. 住宅兜底升级

- fetcher 实例内累计计数：单 job 累计 **3 次** `429`/`anti_bot_challenge` → 置升级标志。
- 用**累计**而非连续（连续易被偶发成功打断，costway 是持续性 429）。
- 阈值 3 的取舍：对持续性 429 快速介入（少吃几百个再切）；对偶发 429 的健康站
  （如 wayfair 仅 99 个 429）单 job 内通常凑不满 3 次，不误升级。
- 升级时先检查 `proxy_pool.has_available("residential")`：
  - 为真 → `ProxyMiddleware` 后续 `before_request` 改用 `residential` tier 取代理
    （即使站点配 `none`）。
  - 为假（代理池空/全被 ban）→ **不升级，记一条诊断**，不静默裸打。

### 与现有熔断的关系

现在 `guard()`/`fail_fast_blocked` 命中 429 会直接抛 `BlockedError` 让整站冷却 12h。
新逻辑要**先给退避 + 住宅兜底留出机会**：撞墙 → 退避重试 → 升级住宅 → 仍不行 → 才熔断。

## 参数（全部走环境变量，默认值如下）

| 参数 | 默认 | 说明 |
|---|---|---|
| `RATE_TIERS`（沿用） | 见上表 | 每 platform 最小请求间隔 |
| `RESIDENTIAL_FALLBACK_THRESHOLD` | 3 | 单 job 累计 429/anti_bot 触发升级 |
| `BACKOFF_MAX_SEC` | 60 | 退避封顶 |
| `RATELIMIT_ACQUIRE_MAX_WAIT` | 30 | 令牌桶单次最大阻塞 |
| `retries`（FetchContext） | magento 类提到 2~3 | 给升级和退避留生效轮次 |

## 错误处理边界

1. 令牌桶 `acquire()` 超过最大等待上限不无限阻塞。
2. 住宅升级前检查代理可用性，无代理则不升级 + 记诊断。
3. 撞墙优先退避 + 兜底，仍失败才走熔断冷却。

## 测试（TDD，纯单元，不打真实站点）

- **令牌桶**：8 线程并发 `acquire`，断言单位时间放行数 ≤ 速率上限。
- **退避**：mock 带 `Retry-After: 30` 的 429，断言 sleep ~30s；无头时断言指数序列
  2→4→8…且不超 60s。
- **住宅升级**：mock 连续 3 个 429，断言第 4 次请求 `proxy_tier` 变 residential；
  代理池空时断言不升级 + 有诊断记录。
- **复位**：断言新 fetcher 实例默认不带升级状态。

## 涉及文件

- `app/antiban.py`：新增 RateLimiter；shoper 速率 0.35→1.0。
- `app/fetching.py`：`_retry_loop` 接入 `acquire`；实现 `RetryMiddleware`
  （退避 + Retry-After + 升级计数）；`ProxyMiddleware` 支持升级后切 tier。
- `app/crawlers/magento.py`：`_fetch_one` 走统一 fetcher 的限速路径（确认并发不再裸跑）。
- 测试：`backend/tests/` 新增限速 / 退避 / 升级用例。

## 落地后验证

部署到 NAS 后，观察 `crawl_failures` 中 costway magento 站的 `http_429` 数量
是否趋近 0；观察住宅代理升级是否只在持续撞墙时发生（控制成本）。
