# 红人搜索需求 · Instagram / TikTok / YouTube / Facebook 多平台

> 业务方：Shulex 红人内部产品线
> 替代标的：Apify (`apify-client` actor 调用) + ScraperAPI
> 当前阶段：MVP 上线，4 平台 native adapter 完成，TikTok 受 2026-05 风控阻塞

## 1. 业务目标

为 Shulex 用户提供 **多平台红人发现 / 画像 / 近期内容** 数据，能力对齐第三方 Apify actor 但完全自主可控，目的是：

1. **去掉外采依赖**：Apify 按 actor compute unit 计费，ScraperAPI 按请求计费，长期成本和限额都受制于人；
2. **统一 schema**：跨平台输出同一份 `InfluencerProfile` + `RecentPost`，下游 AI 打标 / 标杆比对不必处理 4 套异构数据；
3. **可观测的运行生命周期**：复用现有 `/discover/runs` + `/discover/datasets/{id}/items` 接口（Apify-compatible），客户端可无缝迁移。

## 2. 输入 / 输出契约

### 2.1 API 端点

```
POST /discover/runs                       # 创建一次发现任务
GET  /discover/runs/{runId}               # 轮询状态
GET  /discover/datasets/{datasetId}/items # 取结果
```

实现：`backend/app/api/influencer_discover.py`，FastAPI BackgroundTask 异步执行，`RunRegistry` 内存态 + TTL GC。

### 2.2 请求体（`RunRequest`）

```json
{
  "platform": "instagram | tiktok | youtube_about | facebook",
  "params": { "hashtags": [...] } | { "urls": [...] },
  "limit": 20
}
```

| platform | input slot | 备注 |
|---|---|---|
| `instagram` | `hashtags[]` | hashtag → top posts → unique creators |
| `tiktok` | `hashtags[]` | 同上，⚠️ live fetch 阻塞，见 §4 |
| `youtube_about` | `urls[]` | 频道 URL → About tab 详情 |
| `facebook` | `hashtags[]` (用作搜索 query) | FB Pages search adapter |

### 2.3 输出 schema（统一）

`backend/app/influencers/models.py` 定义：

```python
@dataclass
class InfluencerProfile:
    platform: str
    username: str
    user_id: str | None
    display_name: str | None
    bio: str | None
    avatar_url: str | None
    is_verified: bool
    is_business: bool
    category: str | None
    followers: int | None
    following: int | None
    posts_count: int | None
    likes_total: int | None         # TikTok 特有
    contact: Contact                # email / whatsapp / linktree / website
    external_url: str | None
    raw_url: str | None
    fetched_at: str | None          # ISO
    fetched_via: str | None         # web_profile_info / nitter / oembed
    notes: str | None
```

外加 `RecentPost`（Tier 2）字段：post_id / post_url / posted_at / caption / 互动指标。

## 3. 平台适配现状（2026-05-28）

| platform | adapter 路径 | profile | recent posts | hashtag discover | 风控状态 |
|---|---|---|---|---|---|
| **YouTube** | `influencers/youtube.py` + `yt_about.py` | ✅ `fetch_profile()` | ✅ `fetch_posts()` (videos tab) | N/A | 公开 SSR，无 cookie 需求 |
| **Instagram** | `influencers/instagram.py` + `ig_discover.py` | ✅ `fetch_profile()` (web_profile_info → fallback HTML) | ✅ `fetch_posts()` | ✅ hashtag → posts → unique creators | 需 cookie + 住宅代理 |
| **Facebook** | `influencers/facebook.py` (隐式) + `fb_discover.py` | ✅ via Pages search | — | ✅ search query → pages | 需 cookie，已替换 Apify |
| **TikTok** | `influencers/tiktok.py` + `tt_discover.py` | ✅ parser ready（fixture 单测过） | ✅ parser ready | ⚠️ live fetch 阻塞 | 见 §4 |
| **Twitter / X** | `influencers/twitter.py` | ⚠️ partial (nitter mirror fallback) | — | — | nitter 实例不稳定 |

## 4. TikTok 阻塞详情（重点）

**症状**：TikTok 自 2026-05 起，`/tag/{hashtag}` 和 `/@{username}` 页面对**未登录 HTTP 客户端**不再返回 SSR JSON，改用 challenge gate（msToken / WebMSSDK 签名）。

**当前实现**：parser 用 fixture 单测通过（`tests/influencers/fixtures/tt_tag_amazonfba.html`），live fetcher 返回 `[]`。Smoke test 用 `TIKTOK_SMOKE=1` 环境变量门控。

**待实现方案**（任选其一）：

| 方案 | 工程量 | 维护成本 | 命中率预估 |
|---|---|---|---|
| **A. TGE 指纹浏览器 + cookie jar**（首选）| 中 | 中 | 90%+ |
| B. Playwright + stealth + 住宅代理 | 大 | 高 | 70-80% |
| C. msToken / WebMSSDK 签名复刻 | 极大 | 极高，TikTok 变签名就崩 | 95%+ 但易碎 |
| D. 接入第三方付费 API | 小 | 0（外部） | 视服务商 |

**推荐路径**：复用现成的 TGE + cookie 机制（IG/FB 已经在用），加一个 TikTok cookie 文件（`TT_COOKIES_PATH=/app/data/cookies/tt.json`）。

## 5. Cookie 与代理管理

### 5.1 Cookie 来源

- **首选 TGE 指纹浏览器**（memory: TGE 配住宅代理存活率显著更高）
- 流程：TGE 新建干净 profile → 走住宅代理登录 → 导出 cookies JSON
- 路径约定：`/app/data/cookies/{platform}.json` (in-container) → NAS host `/volume1/docker/smart-crawler/app/data/cookies/`
- 权限：`chmod 600`
- **不需重启容器** — adapter 在 401/403 时自动 reload

### 5.2 代理

- 走 NAS smart-crawler 现有 11 个 38.213.x 住宅代理池
- IG/FB 长会话用 cookie 粘性时建议固定 1-2 个代理（同 cookie 同 IP，避免风控触发）
- YouTube 不需代理（公开页）

详见 `backend/app/influencers/README.md` 的 cookie runbook。

## 6. 工程已交付（本次 push 包含）

### 6.1 新增文件

```
backend/app/influencers/
├── README.md                # 平台/cookie/runbook 总览
├── _common.py               # cookie_jar / fingerprint / session helpers
├── cookie_jar.py            # env-path cookie loader + log redaction
├── models.py                # InfluencerProfile / RecentPost / Contact
├── instagram.py             # IG profile + posts (web_profile_info → HTML fallback)
├── ig_discover.py           # IG hashtag → creators
├── tiktok.py                # TT profile + posts parser (live fetch gated)
├── tt_discover.py           # TT hashtag discovery (smoke-gated)
├── youtube.py               # YT channel profile + videos tab
├── yt_about.py              # YT About 解析（替换 ScraperAPI）
├── twitter.py               # X / Twitter via nitter mirror
├── fb_discover.py           # FB Pages search
├── discover.py              # 跨平台 orchestrator + dedupe
├── discover_models.py       # Run lifecycle 数据结构
└── run_registry.py          # In-memory run registry + TTL GC
```

```
backend/app/api/influencer_discover.py    # HTTP API (/discover/*)
```

### 6.2 测试

```
backend/tests/influencers/
├── fixtures/tt_tag_amazonfba.html        # TT parser 离线 fixture
└── （其余按平台分文件）
```

Pytest marker：`@pytest.mark.tiktok_smoke` (default skip, run with `TIKTOK_SMOKE=1`)

### 6.3 Apify 替代审计

```
backend/scripts/apify_clone_audit.py        # 列出依赖的 Apify actor
backend/scripts/apify_github_audit.py       # 扫历史代码里的 Apify 引用
backend/scripts/apify_refine_audit.py       # diff 已替换 vs 待替换
```

## 7. 路线图

| Milestone | 内容 | 状态 |
|---|---|---|
| M1 — 统一 schema | `InfluencerProfile` / `RecentPost` 定义，跨平台对齐 | ✅ |
| M2 — YouTube native | `yt_about.py` 完全替换 ScraperAPI | ✅ |
| M3 — Instagram native | hashtag + profile + posts，cookie 走 TGE | ✅ |
| M4 — Facebook native | Pages search 替换 Apify | ✅ |
| M5 — TikTok parser | parser 完成 + 单测过 | ✅ |
| **M6 — TikTok live fetch** | TGE cookie + 住宅代理走通 hashtag/profile 抓取 | ⚠️ 阻塞中 |
| M7 — Twitter / X native | nitter fallback + 主路 cookie 抓取 | 🚧 partial |
| M8 — 跨平台 dedupe | `discover.py` 按 username/email 合并多平台同人 | ✅ |
| M9 — 生产化 RunRegistry | 现在在 in-memory，需要持久化到 PG 让多 worker 看到 | TODO |
| M10 — Rate limit + retry | adapter 级 429/限流策略统一 | TODO |
| M11 — Webhook 回调 | run 完成后 POST 给客户 endpoint | TODO |

## 8. 验收口径

### 8.1 客户端可见

- `POST /discover/runs` 创建任务，与 Apify run API 调用契约一致
- `GET /discover/runs/{runId}` 状态机：READY → RUNNING → SUCCEEDED / FAILED
- `GET /discover/datasets/{datasetId}/items` 返回 `InfluencerProfile[]` JSON

### 8.2 内部成功度量

- IG hashtag 任务：30 hashtag × 20 creator / hashtag = 600 unique profile / run，命中率 ≥80%
- YouTube channel：100 channel / batch，profile + subs + 30 video，命中率 ≥95%
- FB pages：50 query × 10 page / query = 500 page，命中率 ≥70%（FB 风控较狠）
- TikTok：M6 完成后同 IG 标准

## 9. 决策点

1. **TikTok M6 方案选择**：A (TGE) / B (Playwright) / C (msToken) / D (付费 API)
2. **生产化 RunRegistry**：是否要把 run 状态从内存挪到 PG，让 NAS + MacBook 多 worker 都能见？
3. **客户接口语义**：是否要 100% 镜像 Apify 的 actor / run / dataset / item 命名空间？还是用我们自己的更扁平的语义？

---

文档维护：Shulex 红人产品线
最后更新：2026-05-28
