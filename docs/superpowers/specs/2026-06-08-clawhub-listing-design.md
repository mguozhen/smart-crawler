# 上架 ClawHub — smart-crawler 技能包设计

> 日期：2026-06-08
> 目标：把 smart-crawler 以「ClawHub 技能」形式发布到 clawhub.ai（OpenClaw 注册中心），
> 让 OpenClaw / Claude 等 agent 能搜索并安装该技能，从而连上已托管的远程 MCP 服务。

---

## 背景与约束

- **ClawHub 是什么**：clawhub.ai（steipete / OpenClaw 的注册中心），通过 `clawhub` CLI
  （npm 包 `clawhub`，当前 v0.20.0）发布与安装。它收录的是**技能（skill）**——一个含
  `SKILL.md` 的文件夹，以及 OpenClaw packages，**不直接收录裸的远程 MCP 端点**。
- **smart-crawler 现状**：已托管的远程 MCP 服务，`https://smartcrawler.io/mcp`，
  streamable-http，Bearer（`sck_`）鉴权。仓库根已有 `server.json`（官方 Registry 用）、
  `mcp.json`、`smithery.yaml`。
- **因此「上架 ClawHub」= 打包一个指向该远程 MCP 的 ClawHub 技能**，教 agent 如何连接 +
  挑选工具。技能本身不运行代码、不内嵌密钥。
- **账号**：用户目前**尚无 ClawHub 账号**——注册 + `clawhub login`（浏览器授权）是用户
  亲自完成的前置步骤；本设计把技能包全部备好，使发布只差一条命令。

## 选定方案：连接型技能（Approach A）

一个小的 ClawHub 技能文件夹，`SKILL.md` 教 agent：如何鉴权（env var → Bearer）、MCP
客户端配置片段、以及**精选 7 个旗舰工具**的「何时用」指引。技能不运行任何代码，仅文档 +
配置，指向已上线的远程服务。这与 ClawHub 技能本质（文本 SKILL.md）和本服务本质（远程
streamable-http）都吻合，安全扫描最干净（只声明 1 个 env var、无二进制）。

（已否决：B 内嵌 npm 安装助手——多一个可执行依赖会被扫描标记；C 按主题拆两个技能——
与「单 slug + 精选」目标冲突。）

## 文件结构

放在仓库内，与现有 `server.json` / `mcp.json` 一起版本化：

```
clawhub/smart-crawler/
  SKILL.md          ← 技能主体（frontmatter + 正文）
  reference/
    tools.md        ← 精选工具目录：输入 + 示例
    setup.md        ← Claude / Codex / Cursor 的 MCP 客户端配置
```

## SKILL.md frontmatter（驱动 ClawHub 索引 + 安全扫描）

- `name: smart-crawler`
- `description:`（触发导向）：Use when an agent needs cross-border e-commerce competitor
  intelligence, VOC / review sentiment, Amazon ASIN analysis, Google Shopping landscape,
  on-demand URL scraping / crawling, or Reddit subreddit growth playbooks — via the hosted
  smart-crawler MCP server.
- 运行时需求**如实声明**：env var `SMARTCRAWLER_API_KEY`；无二进制；需访问网络
  `smartcrawler.io`。

## SKILL.md 正文结构

1. 这是什么 + 远程端点（`https://smartcrawler.io/mcp`，streamable-http，Bearer 鉴权）。
2. 配置：在控制台「API 接入」生成 key → `export SMARTCRAWLER_API_KEY=sck_...` →
   MCP 客户端配置（详见 `reference/setup.md`）。
3. 精选工具目录（7 个旗舰），每个一行 + 何时用，完整细节在 `reference/tools.md`：
   - `reddit_subreddit_playbook` — 任意 subreddit 头部贡献者增长 playbook（一站式）。
   - `search_competitor_products` — 竞品目录检索（品牌 / 国家 / 关键词 / 价格 / 促销）。
   - `get_voc_reviews` — Trustpilot / Reviews.io / Google Maps 消费者 VOC 评论。
   - `amazon_voc_report` — 按 ASIN 拉评论并生成 AI VOC 分析（痛点 / 亮点 / listing 优化）。
   - `competitor_landscape` — Google Shopping 某关键词的商家份额排名。
   - `scrape_url` — 单 URL 抓取（5 分钟 agent 记忆）。
   - `query_warehouse` — 仓库优先的自然语言查询（0 credit）。
4. 鉴权说明 + 计费提醒（`crawler:scrape` 域工具消耗 credit；warehouse / VOC 读取多为低/零）。

## 鉴权处理（env var，文档化）

SKILL.md 指示 agent：读取 `SMARTCRAWLER_API_KEY`，以
`Authorization: Bearer $SMARTCRAWLER_API_KEY` 发送。**发布文件中不含任何真实密钥。**

## 发布流程（浏览器登录由用户执行）

1. *(前置，用户)* 在 clawhub.ai 注册 → `npx clawhub login`（浏览器）→ 拿到 publisher handle。
2. `npx clawhub whoami` 确认登录。
3. `npx clawhub skill publish clawhub/smart-crawler --slug smart-crawler --owner <handle> \
      --version 0.1.0 --tags latest --changelog "Initial release"`
4. `npx clawhub inspect smart-crawler` 验证已上线 + 查看安全扫描结果。

## 验证

- `npx clawhub inspect smart-crawler` 显示已发布的元数据与文件清单。
- `npx clawhub scan` 查看扫描报告，确认仅声明 1 个 env var、无意外发现。

## 非目标（YAGNI）

- 不内嵌 / 不重写 `packages/mcp` 安装助手（它属于 npm）。
- 不拆分主题技能。
- 不改动后端 MCP 服务或工具实现——本次纯打包 + 发布。
