# Firecrawl 整合研究 · 2026-05-23

> **结论一句话**：**不整合核心**（AGPL-3.0 红线 + Fire-engine 只在 cloud），**借鉴它的 API 设计形态**做 smart-crawler v2 对外接口。

---

## Firecrawl 速览

- **GitHub**：[mendableai/firecrawl](https://github.com/mendableai/firecrawl) · 123k stars · 5,478 commits
- **当前版本**：v2.10（2026-05-15）
- **技术栈**：TS 66% + Python 17% + Rust 5%（Fire-engine 用 Rust）
- **核心 7 端点**：`/scrape` `/crawl` `/map` `/batch_scrape` `/extract` `/search` `/interact`
- **设计哲学**：markdown-first 输出，JSON schema 直接抽，LLM-native

## 致命问题：License + 自部署能力

| 维度 | 状态 | 影响 |
|---|---|---|
| **核心 license** | **AGPL-3.0** | 你已 ruled out MediaCrawler 同等 license。AGPL 触发：smart-crawler 以网络服务对外暴露含 Firecrawl 代码的功能，必须开源整个 smart-crawler |
| SDK license | MIT | 只能 SDK 调用「别人托管」的 Firecrawl，不能嵌入卖 SaaS |
| **Fire-engine** | **cloud-only** | 反爬高级能力（破 IP 封禁/robot detection）self-host 用不了 |
| Self-host docker | 三服务（Redis + Playwright + API:3002） | NAS 跑得动，但反爬能力 ≈ 我们现状（Scrapling + curl_cffi） |
| `/agent` `/browser` 端点 | self-host 不可用 | 唯一 cloud 增量被锁死 |
| `/extract` LLM | 需自己挂 OPENAI_API_KEY / Ollama | 不依赖 Firecrawl 也能做 |

## vs Crawlee-Python（我们已定的底座）

- **Crawlee**（Apache-2.0）= 抓取**框架**：request queue / session pool / storage
- **Firecrawl**（AGPL-3.0）= 抓取**服务**：包好的 7 端点
- **非替换关系**。Crawlee 仍是底座，Firecrawl 只能作为某个 outbound HTTP fetcher

## 3 个切入点判定

| 切入点 | 可行性 | 工作量 | 风险 | ROI |
|---|---|---|---|---|
| A. 替换 `vidaxl.py` 破 401 封禁 | **低**。self-host 无 Fire-engine = 等于没区别；cloud 版可能破但 AGPL + 按量收费 | 1 天 PoC | 401 仍存在 / cloud 成本叠加 | **负** |
| B. LLM-extraction 加速 VIVO 28 字段 | **中**。但自己挂 Ollama / GPT + instructor + Pydantic 更轻 | 2 天 | 多一层 HTTP 跳 + AGPL 污染 | **低** |
| C. Batch crawl 扫 sitemap | **中高**。`/map` 端点确实快 | 1 天接入 | self-host 限 `MAX_CPU=0.8` 自我限流；商用 AGPL | **中**，但 Crawlee 的 `SitemapRequestLoader` 已能做 |

## 借鉴 Firecrawl 做的事（不整合，但学）

1. **API 形态**：smart-crawler v2 对外接口可参考 `/scrape /map /extract`（markdown-first + JSON schema 直出）
2. **batch_scrape 异步模型**：客户提交任务 → 立即返 task_id → 异步出结果 → webhook 通知。这是 SaaS 商用化的标准模式
3. **/map 先返 URL 集合再 crawl**：分两阶段（发现 vs 抓取）有助于客户预估成本（按 record 计费透明）
4. **LLM extraction 用 JSON schema 而非 free text**：我们自己用 instructor + Pydantic 实现，绕开 AGPL

## 决策

- ❌ **不引入 Firecrawl 任何代码或 Docker 镜像**
- ✅ smart-crawler v2 对外 API 参考它的 7 端点形态（属于设计模式参考，非衍生作品）
- ✅ Crawlee（Apache-2.0）继续做底座
- ✅ LLM 抽取自己写：`instructor + Pydantic + claude-haiku-4-5`
- ✅ 反爬继续 Scrapling + curl_cffi + 10 商业代理池

## 行动项

| 优先级 | 任务 | 工期 |
|---|---|---|
| P0 | smart-crawler v2 API design doc（参考 Firecrawl 7 端点） | 1 天 |
| P1 | `instructor + Pydantic` LLM-extract POC（替代 Firecrawl `/extract`） | 2 天 |
| P2 | 按 record 计费 + batch_scrape 异步模式调研 | 1 天 |
