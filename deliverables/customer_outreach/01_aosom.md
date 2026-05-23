# 客户 outreach #1 · 遨森（Aosom）

> **优先级**：🥇 最高（命中度 90% · RFP-ready）
> **目标**：把现有 smart-crawler 包装成投标方案，2 周内签订年单 ¥10-30w

---

## 客户画像

- **公司**：遨森电商（Aosom）— 跨境家居电商品牌
- **覆盖**：12 国自营站（US/UK/DE/FR/IT/ES/IE/RO/PT/NL/PL/CA）
- **痛点**：竞品（SONGMICS/Costway/Homary/Yaheetech/Vidaxl/Flexispot 等 9 品牌 46 站）数据散落，无统一监测
- **决策者**：可能是 CMO 或品类总监
- **预算可批级**：年单 ¥10-50w（toB SaaS 决策可一次签 2 年）

---

## 邮件模板（中文版）

```
主题：smart-crawler 已交付 46 站监测能力 · 邀请试用

[客户名] 您好，

我是 boyuan@solvea.cx，smart-crawler 创始人。我们读过贵公司「海外独立站电商网站监测项目」的需求规格说明书 v1.0，已按 SRS 完成 90% 能力建设，邀请贵团队试用：

▎ 已建能力
• 46 个独立站 SKU 监测（SONGMICS 6 站 + Costway 9 站 + Homary 5 站 + Yaheetech 2 站 + Vidaxl 12 站 + Flexispot 9 站 + Best Choice Products + VonHaus + Woltu）
• 21 个第三方评论平台（Trustpilot 9 站 + Google Map 7 站 + Reviews.io 等）
• Google Shopping 关键词竞品占有率
• 价格曲线 / 促销活动 / 新品识别 / 销量营收估算（评论增量倒推法）
• 多语种原文 NLP（PT/RO/PL/NL 等小语种，不翻译）
• MCP 接入（AI Agent 可直接调用）

▎ 实测数据（截至本邮件发出时）
• 总 SKU: 104,576
• 平台覆盖率: 2.47%（持续提升中）
• 代理池: 10 个商业住宅代理 · 14,810 次调用 0 失败
• 关键站点（Vidaxl 12 站）已完成 9/12

▎ 投标建议
1. 一次签 1-2 年框（避免逐项目谈判）
2. 按品牌 + 国家组合定价（¥3-5w / 品牌 / 年）
3. SLA 承诺：覆盖率 95% + 数据延迟 5min + 字段完整率 90%

▎ 立即可看的 demo
• 看板预览：https://smartcrawler.io/app（账号申请回邮）
• 战略方案：https://raw.githack.com/mguozhen/smart-crawler/feature/customer-design-cards/deliverables/strategy_v2.html
• 数据覆盖率仪表盘：登录后「数据覆盖率」tab 实时

约个 30min 电话过一遍系统？本周内任意时段可。

Best，
Boyuan Yuan
smart-crawler 创始人
+86 [手机]
boyuan@solvea.cx
```

---

## 投标交付方案（一页）

### 我们已建（90%）

| 模块 | 状态 |
|---|---|
| 46 站独立站 SKU 采集 | ✅ 全部就绪 |
| 价格曲线 + 促销识别 | ✅ |
| Trustpilot/Google Map/Reviews.io | ⚠️ 部分（继续补全） |
| Google Shopping 占有率 | ⚠️ 部分 |
| Dashboard 5 大看板 | ✅ |
| RESTful API + Excel/CSV/JSON/ZIP 导出 | ✅ |
| 多语种 NLP（不翻译） | ✅（用 flatkey.ai + claude-haiku） |

### 缺口 + 补全节奏（2-4 周）

| 缺口 | 工期 |
|---|---|
| 补全 21 评论平台（Reviews.io / TrustedShop / Opiniones Verificadas） | 2 周 |
| Google Shopping 完整覆盖 | 1 周 |
| 多语 NLP 小语种 fine-tune（PT/RO/PL/NL） | 2 周 |
| AWS WAF 反爬强化 | 1 周（已用 Scrapling StealthyFetcher） |
| 自动 alerting + 邮件日报 | 1 周 |

### 报价档位

| 档位 | 年费 | 包含 |
|---|---|---|
| **基础**（推荐起步） | ¥120,000 | 46 站 + 21 评论 + 周报 + 8x5 客服 |
| **专业** | ¥240,000 | 加 Google Shopping + 实时 alerting + API + 24x7 |
| **企业** | ¥480,000 | 加自定义品牌 + Slack 集成 + 定制报告 + 专属 CSM |

### SLA 承诺

- 覆盖率 ≥ 95%
- 字段完整率 ≥ 90%
- 数据延迟 ≤ 5 min（价格/促销）
- 系统可用率 ≥ 99.5%
- 新站点接入 1-2 天

---

## 谈判要点

1. **不要谈"按 record 计费"** — 客户预算是项目制，年单更符合
2. **强调 SRS 已完成 90% 命中** — 别的乙方要从 0 写代码
3. **demo 优先** — 让客户登录看真实 Dashboard，比 PPT 强 10 倍
4. **2 年框打折** — 第 2 年 8 折，锁定 LTV

---

## Next Action（你做的）

1. 发邮件（中英文版本可选）
2. 准备 30min 演示会议
3. 准备 demo 账号（创建 smartcrawler.io 用户 + 给品牌专属 API key）

---

**预期反馈时间**：3-5 工作日内回邮 / 1-2 周内 demo / 4-6 周内签约
