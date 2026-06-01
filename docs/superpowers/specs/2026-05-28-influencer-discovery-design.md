# Influencer Discovery — 替换 Apify + ScraperAPI

**日期**：2026-05-28
**目标**：smart-crawler 原生实现 TikTok / Instagram / Facebook hashtag 发现 + YouTube About 邮箱网站抽取，下线对 Apify 和 ScraperAPI 的付费依赖。**当日完成**，内部 Node 调用方等用。

## 范围

四个平台，全部上线：

| 平台 | 输入 | 输出形态 | 当前外部依赖 |
|---|---|---|---|
| TikTok | hashtags[] | 创作者列表（含 followerCount / email / websiteUrl） | Apify `clockworks/tiktok-scraper` |
| Instagram | hashtags[] | 创作者列表 | Apify `apify/instagram-scraper` |
| Facebook | 关键词（pages search） | Page 列表 | Apify `apify/facebook-pages-scraper` |
| YouTube About | profileUrl[] | `{email, websiteUrl}` 列表 | ScraperAPI |

YouTube 频道搜索本身继续用 Google YouTube Data API（免费配额内），**只换 About 页补抓**。

## 架构

```
Node 调用方
   │
   ▼
FastAPI · backend/app/api/influencer_discover.py
  POST /discover/runs                  ──┐
  GET  /discover/runs/{runId}            │  in-memory RUNS dict (RLock)
  GET  /discover/datasets/{datasetId}/items ◀──┘
   │
   ▼ BackgroundTasks
backend/app/influencers/discover.py     (orchestrator · 去重 · 字段映射)
   │
   ├── tt_discover.py     curl_cffi · residential proxy
   ├── ig_discover.py     curl_cffi + IG cookie · residential
   ├── fb_discover.py     curl_cffi + FB cookie · residential
   └── yt_about.py        curl_cffi · direct（无代理）
   │
   ▼ 复用现有
backend/app/proxy.py · backend/app/antiban.py · backend/app/influencers/_common.py
```

**关键决策**

- 不新增容器，沿用现有 FastAPI 进程；新 router 注册到 `main.py`
- run/dataset 状态用进程内 dict，worker 重启会丢——可接受（重跑成本几秒）
- 现有 `influencers/{tiktok,instagram,youtube}.py` 的 `fetch_profile(known_handle)` **保留**，作为发现后回查 followerCount / email / websiteUrl 的二级请求

## HTTP 契约

```
POST /discover/runs
  body (hashtag platforms):
    {"platform":"tiktok|instagram|facebook",
     "hashtags":["amazonfba","amazonseller"],  # facebook 用 keywords
     "limit":38}
  body (yt_about):
    {"platform":"youtube_about",
     "urls":["https://www.youtube.com/@xxx/about", ...]}
  → 200 {"runId":"<uuid>","datasetId":"<uuid>","status":"PENDING"}
       （datasetId == runId，简化）

GET /discover/runs/{runId}
  → 200 {"status":"PENDING|RUNNING|SUCCEEDED|FAILED",
         "itemCount":N, "error":null|str,
         "startedAt":"...", "finishedAt":"..."}

GET /discover/datasets/{datasetId}/items?limit=&offset=
  → 200 [CreatorRecord, ...]
```

## 输出 Schema

```python
@dataclass
class CreatorRecord:
    channelId: str          # 主键 · 平台前缀+handle，去重用
    name: str | None        # 显示名
    platform: str           # "TikTok" | "Instagram" | "Facebook" | "YouTube"
    profileUrl: str         # 主页可点链接
    handle: str | None      # @username 不带 @
    followerCount: int | None
    email: str | None
    websiteUrl: str | None
```

**channelId 命名约定**

| 平台 | 格式 | 例 |
|---|---|---|
| TikTok | `@{handle}` | `@sellerjoe` |
| Instagram | `ig:{handle}` | `ig:sellerjoe` |
| Facebook | `fb:{handle或pageId}` | `fb:sellerjoe` / `fb:123456` |
| YouTube | `UC...`（频道 ID）/ `@handle` 回退 | `UCxxxx` |

**YouTube About** 走同一 runs/datasets API，但 dataset item 缩成两字段（按 urls 顺序对齐）：

```python
{ "email": "...", "websiteUrl": "..." }
```

**Apify → CreatorRecord 字段映射**（TikTok 举例，IG/FB 同模式）

| Apify 原字段 | CreatorRecord |
|---|---|
| `authorMeta.uniqueId` | `handle`，`channelId = "@" + uniqueId` |
| `authorMeta.nickName` | `name` |
| `authorMeta.fans` / `followers` / `followerCount` | `followerCount`（首个非空） |
| `authorMeta.signature` | 正则提 email → `email` |
| `authorMeta.bioLink` | `websiteUrl` |
| — | `profileUrl = https://www.tiktok.com/@{handle}` |

**去重**：orchestrator 在落库前按 `(platform, handle)` 去重。
**缺失策略**：`followerCount` / `email` / `websiteUrl` 缺值 → `null`。`channelId` / `platform` / `profileUrl` 是硬必填，缺则丢弃 + 记 notes。

## Cookies · 代理 · 反爬

### Cookie 存储
- **格式**：JSON 数组 `[{"name":"sessionid","value":"...","domain":".instagram.com","path":"/"}, ...]`（Playwright `context.cookies()` 导出可直接用）
- **路径**（env 指向）：
  ```
  IG_COOKIES_PATH=/app/data/cookies/ig.json
  FB_COOKIES_PATH=/app/data/cookies/fb.json
  ```
  复用现有 `./data:/app/data` 卷挂载（docker-compose.yml 已有，无需改容器配置）。NAS 宿主路径 `/volume1/docker/smart-crawler/app/data/cookies/`，文件权限 600，git 忽略
- **加载时机**：首次调用时读盘缓存到模块级变量；鉴权失败时清缓存，下次重读（换文件立刻生效，无需重启）

### 鉴权失败检测

| 平台 | 失败信号 | 动作 |
|---|---|---|
| Instagram | 302 → `/accounts/login` 或 401/403 | 抛 `CookieExpiredError("instagram")`，run.error = `cookies_expired_instagram` |
| Facebook | 302 → `/login.php` 或 `/checkpoint/` | 抛 `CookieExpiredError("facebook")` |
| TikTok | 403 + 响应含 DataDome / verify-bot 关键字 | 换代理 IP 重试 1 次；仍失败抛 `BlockedError` |
| YouTube About | 200 但 HTML 不含 `ytInitialData` | 换 UA 重试 1 次；仍空回退 None |

cookie 失败 → DingTalk 群推送告警 `⚠️ {platform} cookie 失效，请更新 {path}`（复用 `send_group.py`）。

### 请求头 / proxy tier

| Adapter | UA | 额外头 | proxy_tier | 节流 |
|---|---|---|---|---|
| tt_discover | Chrome 131 desktop | `Referer: https://www.tiktok.com/` | residential | 2–4s |
| ig_discover | Chrome 131 desktop | `x-ig-app-id: 936619743392459`, `x-asbd-id: 129477`, `Referer: https://www.instagram.com/` | residential | 3–6s |
| fb_discover | Chrome 131 desktop | `Referer: https://www.facebook.com/` | residential | 3–6s |
| yt_about | Chrome 131 desktop | `Accept-Language: en-US,en;q=0.9` | direct | 1–2s |

代理走现有 `app.proxy.get_proxy(tier)`（10 个 38.213.x 住宅 IP）。

### 频次熔断
复用 `app.antiban.check_blocked(status, where)` + `ip_record()`。每平台每 hashtag 上限 5 页，单 run 总请求数硬上限 100，超出截断并 `notes += "truncated_at_limit"`。

### 安全
- cookie 文件 chmod 600，**绝不写日志**；日志里 cookie 字符串自动用 `[REDACTED]` 替换
- cookie 进 `.gitignore`、不进容器镜像、运维手动放置

## 错误处理

| 场景 | run.status | run.error | items |
|---|---|---|---|
| hashtag 命中 0 结果 | SUCCEEDED | null | `[]` |
| 1 个 hashtag 报错，其它成功 | SUCCEEDED | null | 成功部分 + `notes` 记失败 hashtag |
| 全部 hashtag 报错 | FAILED | `all_hashtags_failed: <reason>` | `[]` |
| cookie 失效 | FAILED | `cookies_expired_<platform>` | partial |
| 超过 100 请求硬上限 | SUCCEEDED | null | 截断部分，`notes` 含 `truncated_at_limit` |
| 单 adapter HTTP 5xx | 内部重试 2 次（指数退避 2/4s） | — | — |

### 超时
- 单 HTTP 请求 timeout 20s
- 整个 run 软上限 5 min；BackgroundTask 内自定 deadline，超时标 `FAILED: deadline_exceeded`
- run/dataset 在内存里保留 1 小时后 GC（后台 thread 每 5 min 扫一次）

## 测试

### 1. 单元测试（mock HTTP，fixture 文件落盘）
`backend/tests/influencers/`：
- `test_tt_discover_parser.py`：固化 tag 页 HTML → 断言 ≥10 个 authorMeta
- `test_ig_discover_parser.py`：固化 `tags/web_info` JSON → user 提取正确
- `test_fb_discover_parser.py`：固化 search/pages HTML → pageId/handle 提取
- `test_yt_about_parser.py`：固化 about 页 HTML → email + websiteUrl 提取
- `test_creator_record_mapper.py`：每平台 raw → CreatorRecord 字段映射

### 2. 集成 smoke（真网络，每平台 1 个 hashtag，limit=5）
- 接入 `scripts/regression_test.sh`：`pytest -m smoke backend/tests/influencers/`
- IG/FB 用 `@pytest.mark.skipif(no_cookies)` 自动跳过

### 3. HTTP 契约测试（FastAPI TestClient）
- POST runs → poll until SUCCEEDED（30s 超时）→ GET dataset → 断言 ≥1 条含必填字段

### 4. 完工人工验证（部署后）
```bash
for p in tiktok instagram facebook; do
  curl -X POST http://192.168.1.80:8077/discover/runs \
    -H 'Content-Type: application/json' \
    -d '{"platform":"'$p'","hashtags":["amazonfba"],"limit":10}'
done
curl -X POST http://192.168.1.80:8077/discover/runs \
  -H 'Content-Type: application/json' \
  -d '{"platform":"youtube_about","urls":["https://www.youtube.com/@MrBeast/about"]}'
```

## 部署（NAS-only）

backend 在容器里以 `./backend:/app/backend:ro` 挂载（已有 compose 配置），代码改完 scp 覆盖 + 重启就行，无需 rebuild image。

1. 本地写代码 + 单元测试 pass
2. `scp` 新文件到 `solvea@192.168.1.80:/volume1/docker/smart-crawler/app/backend/...`
3. 放好 cookies：`scp ig.json fb.json solvea@192.168.1.80:/volume1/docker/smart-crawler/app/data/cookies/`
4. SSH 进 NAS：`docker compose restart api`（service name 见 compose）
5. 容器内跑 smoke：`docker compose exec api pytest -m smoke backend/tests/influencers/`
6. 跑「完工人工验证」curl 套件，结果存档 `/volume1/docker/smart-crawler/app/deliverables/discover_smoke_2026-05-28.json`
7. 告诉调用方切 base URL：`https://api.apify.com → http://192.168.1.80:8077`

## 文档交付

`backend/app/influencers/README.md` 写：
- 4 个 platform 的 input schema 例子
- CreatorRecord 输出
- Cookie 刷新 runbook（本地登录 IG/FB → Playwright 导出 cookie → scp 到 NAS）

## 非目标（YAGNI）

- 不做持久化（dataset 入数据库）—— 进程内 dict 够用
- 不做 Playwright fallback —— 若 IG/FB 用 cookie 也卡住，明天再上
- 不做多租户 / API Key 鉴权 —— 内网直连
- 不做 YouTube 频道搜索 —— 继续走 Google Data API
- 不实现 Twitter / X —— 不在本次替换范围
