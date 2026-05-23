# W2 Sprint 草案 · 2026-05-25 ~ 06-01

> **状态**：草案，等用户拍板。
> **前置依赖**：W1 已完工（Crawlee 底座、计费 schema、4 客户邮件、代理池）。

---

## W2 5 大里程碑

### 1. YouTube fetcher 生产化（2 天）
- 用 `yt-dlp` PyPI 包 + YouTube Data API v3 双路径
- 采集字段：标题/描述/views/likes/评论/上传时间/频道粉丝/视频时长
- 支持单 video / 单 channel / subscription / 关键词搜索
- ASR pipeline pre-work：取 audio track 准备 W5 Whisper 接入

### 2. TikTok fetcher 生产化（2 天）
- 用 `Evil0ctal/Douyin_TikTok_Download_API`（GitHub 9.5k⭐ Apache-2.0）
- 采集：视频 + 评论 + 点赞 + 转发 + KOL 信息
- 反爬：用 10 代理池轮换，遇 风控 切代理

### 3. 数据库 PostgreSQL 迁移（1.5 天）
- 当前 SQLite 文件已 800MB+，扩容到 PG
- 新增 `pg_partman` 按月分区 product / price_history / reviews
- 迁移脚本 `migrate_sqlite_to_pg.py`
- 双写过渡 24h 验证一致性

### 4. Lazada / Shopee fetcher 启动（2 天）
- VIVO POC（5/6）必须项
- Lazada：开放平台 + 反爬走 Playwright + 代理
- Shopee：Mobile App API（更稳）+ Playwright fallback

### 5. 28 字段 schema 落地 + 6 个新字段（2 天）
- VIVO POC 必须：is_main / 互动量子项 / 国家识别 / 水军识别 / KOL 层级 / 关键词命中
- 数据库 schema 加 6 列 + 编写填充逻辑
- 28 字段 Excel 导出 template

---

## 资源需求

| 工种 | 人数 | 工期 |
|---|---|---|
| 后端（Crawlee + PG 迁移） | 1.5 | 4 天 |
| 社媒爬虫（YouTube + TikTok） | 1.5 | 4 天 |
| 反爬调度（代理 + Lazada/Shopee） | 1 | 4 天 |
| 全 W2 | **4 人周** | 含 buffer |

---

## Risk + Mitigation

| Risk | Mitigation |
|---|---|
| TikTok fetcher 反爬触发 | 10 代理 + Sleep 抖动 + User-Agent 池 |
| YouTube API quota 用完 | quota 监控 + 多 API key 轮换 |
| PG 迁移双写不一致 | shadow read 24h + diff alert |
| Lazada/Shopee mobile API 失效 | Playwright fallback 同步开发 |

---

## Next Action

1. 用户：确认 W2 启动（已经过 W1 完工的前提）
2. 用户：HR 启动 4-6 岗招聘（重要：W2 4 人周已经超载小团队上限）
3. 我们：开 W2 启动会，拆票到 4 个 sub-issue
