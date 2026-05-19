# origin/main 分叉合并方案

> 2026-05-19 · 给 Hunter（mguozhen@gmail.com）
> 接手人：Bo Yuan / Solvea MailOutreach Agent

## TL;DR

origin/main 跟另一台机器上的本地 main **在 5/17-5/18 同一时间窗**独立修了同一个 bug，
两份方案功能等价、实现不同。NAS 上跑的是本地版本，PG 中数据吻合 origin 版本描述。
推荐 **方案 C：你 review 本地分支，决定要不要 merge**。

## 时间线 + 分叉

共同祖先：`8a60290 docs: 项目交接文档 HANDOFF.md`

```
                      ┌── 79fa6f2  feat(crawlers): 价格/币种解析 + Shopping 双路           ← 2026-05-19
                      ├── ef30754  chore(auth): admin 用户名默认 aosen → admin            ← 5/17
                      ├── 6235bb1  fix(reviews): reviews_io / google_shopping 修复       ← 5/17 23:17
8a60290 ──┤
                      ├── 7b583f3  fix: 评论渠道按 site+platform 消歧                       ← 5/18 01:34
                      ├── 3fedaaa  docs: HANDOFF 更新 — reviews_io 1670                   ← 5/18
                      └── fd0044e  docs: HANDOFF — Google Maps gosom 5584                ← 5/18 ← origin/main HEAD
```

本地 main HEAD = `79fa6f2`，已 push 到备份分支：
**`origin/local/may19-nas-snapshot`** (commit `79fa6f2`)

## 两个独立修复的对比

| 维度 | 本地 `6235bb1` | origin `7b583f3` |
|---|---|---|
| 时间 | 2026-05-17 23:17 PT | 2026-05-18 01:34 PT |
| 作者机器 | flatkeys-MacBook-Air-2 | mguozhen | 
| 描述 | "site 同名多平台时按 site+platform 双键消歧" | "渠道 dict 直传 + site 名跨平台时要求指定 platform" |
| 实测效果 | reviews_io aosom_uk 0→1670 条 | reviews_io 1670 / Google Maps 5584（commit msg） |
| 接口风格 | 改 `run_review_channel` 的 site 查询逻辑 | 改 `run_review_channel` 的参数签名 |

**功能等价的证据**：NAS 上当前跑的是 `6235bb1` 版（review_runner.py md5 cc671bf9...
匹配本地，不匹配 origin 8076cfcf...）。PG 中 reviews 表 trustpilot 1346 +
reviews_io 1672 + google_map 5584，与 origin 的 commit 描述吻合。这说明：
- 本地 6235bb1 + 不知通过什么路径补的 Google Maps gosom 数据
- 或：Hunter 当时在 NAS 上手动跑过 gosom 工具补数据，跟 review_runner 改动无关

无论哪种，**两边修复都拿到了同样的数据扩量结果**。

## 我刚加的 79fa6f2

跟 reviews 无关，纯 crawler 改动：
- `google_shopping`：重写为 scrapling stealth + Bing Shopping 双路兜底（不依赖付费 SERP API）
- `homary`：智能价格解析（兼容欧式 `94,99 €` 美式 `$94.99`）
- `shopify`：按 site.country 推断 currency

NAS 容器已部署且数据库依赖装齐（scrapling 0.4.8 + curl_cffi 0.15.0）。
**Google Shopping 双路尚未实战验证**（这是 P2.1）。

## 推荐方案：C —— PR review

**理由**：reviews 重构是 Hunter 主导的，由你来判断哪个 review_runner.py 接口更优。
我不应该单方面 cherry-pick / drop。

```bash
# 你在新机器上：
git fetch origin
git checkout local/may19-nas-snapshot

# 看 reviews diff
git diff origin/main..local/may19-nas-snapshot -- backend/app/review_runner.py backend/review_channels.yaml

# 看我新加的 crawler diff
git diff origin/main..local/may19-nas-snapshot -- backend/app/crawlers/
```

三选一：
- **C1**：你的 `7b583f3` 更优 → 把 `local/may19-nas-snapshot` 上的 6235bb1 review 改动 drop，
  保留 `ef30754` + `79fa6f2` cherry-pick 到 origin/main → 同步部署到 NAS
- **C2**：本地 `6235bb1` 更优 → 把 origin 的 `7b583f3` revert，把整个 backup 分支 merge
- **C3**：两个独立合理 → 合 review_runner.py 时手动取并集（双键消歧 + dict 接口都保留）

## 备选方案

- **A** (保守)：暂不合并，两边各跑各的。**坏处**：以后每次改 review_runner 都要解冲突。
- **B** (单边推平)：force-push 一边盖另一边。**风险**：丢失对方的工作。**不推荐**。

## 影响范围

合并工作只涉及：
- `backend/app/review_runner.py`（review 调度逻辑）
- `backend/review_channels.yaml`（评论渠道配置）
- 其它文件无冲突

合并后 NAS 部署：手 scp 改的 review_runner.py + restart smart-crawler 容器（~10 秒中断）。

## 联系

我可以执行你的决策，只需要你在 PR / Issue 里 ack 选 C1/C2/C3 中的哪条路。
现状没紧迫性 —— 数据照常进库，监控正常。
