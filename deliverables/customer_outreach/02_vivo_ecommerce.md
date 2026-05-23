# 客户 outreach #2 · VIVO 海外电商论坛舆情

> **优先级**：🥈 高（命中度 40% · 量级大）
> **目标**：参与 POC 测试（5/6 实战窗口 28 字段） → 中标年单 ¥50-100w

---

## 客户画像

- **公司**：VIVO 通讯（含 iQOO 子品牌）
- **场景**：东南亚 + 拉美 + 欧洲 14 国 14 手机品牌竞品监测
- **POC 商品清单**：615 个商品 URL（Lazada 226 + Shopee 204 + Amazon 185）+ 7 个 subreddit + GSMArena
- **POC 测试时间**：2026/5/6 14:00-16:00 实时窗口，16:30 前交付舆情反馈表

### 平台清单（POC 必须）
| 平台 | smart-crawler 现状 |
|---|---|
| Amazon MX/BR/ES | ⚠️ 有 Amazon US/UK 框架，需扩 3 国 |
| Lazada ID/TH/MY | ❌ 缺，新建（2-3 天） |
| Shopee ID/TH/MY | ❌ 缺，新建（2-3 天） |
| Flipkart IN | ❌ 缺，新建（2-3 天） |
| Reddit | ✅ 已有（7 subreddit） |
| GSMArena | ❌ 缺，新建（1 天） |

---

## 邮件模板

```
主题：VIVO 电商论坛舆情 POC · smart-crawler 应答

VIVO 商研 [负责人] 您好，

我是 boyuan@solvea.cx，smart-crawler 创始人。我们看到 VIVO 海外电商论坛类舆情监测项目的 POC 测试题（5/6 实战窗口 28 字段输出），希望参与应答。

▎ smart-crawler 现状
• 已建 46 站独立电商 + Amazon VOC + Reddit playbook（含 7 subreddit）
• MCP-first 架构（AI Agent 直接调用）
• 10 个商业住宅代理池 · 0 失败
• 多语种原文 NLP（PT/RO/PL/NL 已支持，可快速扩 ID/TH/MY）

▎ POC 应答规划
• Amazon (MX/BR/ES)：已有 US/UK 框架，扩 3 国 2 天
• Lazada (ID/TH/MY)：新建 crawler，3-5 天
• Shopee (ID/TH/MY)：同上
• Flipkart (IN)：3 天
• Reddit + GSMArena：已有 / 1 天

▎ 28 字段交付
我们的 product_dict 已含 22/28，缺的 6 个字段（is_main/互动量/国家/parent_id/转发量/water）可在 POC 前一周补齐。

▎ 关于 100 人门槛
smart-crawler 团队当前 [X] 人。如果团队规模是必要门槛，我们可联合 [合作伙伴] 一起投标，但技术主体由 smart-crawler 完成。

▎ 邀请
我们能否申请加入 POC？我们提供：
1. 免费的 POC 阶段试跑（不收费用）
2. 5/6 实战窗口前 1 周完成所有平台适配
3. 测试通过后提供完整 API + 28 字段 schema

约个 30min 电话过一遍方案？

Best，
Boyuan Yuan
smart-crawler 创始人
+86 [手机]
boyuan@solvea.cx
```

---

## POC 应答方案（一页）

### POC 测试要求复述
- **时间**：2026/5/6 14:00-16:00（2 小时实时窗口）
- **关键词**：vivo/iqoo/originos/samsung/oneui/xiaomi/redmi/hyperos/oppo/coloros/iphone/ios（共 12 个）
- **平台**：Amazon MX/BR/ES + Lazada ID/TH/MY + Shopee ID/TH/MY + Flipkart IN + Reddit + GSMArena + 官方论坛/电商
- **交付**：16:30 前交付舆情反馈表（28 字段 Excel + 接口 + 接口文档）

### 28 字段输出 schema

```
platform / sitename / id / parent_id / title / content / is_main / 
互动量(likes/comments/shares/views/saves/repins) / 国家 / 语言 / 
帖子时间 / 采集时间 / 作者 / 作者粉丝数 / KOL层级 / 媒体类型 / 
正负面情感 / 情感分数 / 主题标签 / 关键词命中 / 转发量 / 水军识别
```

smart-crawler 现有 22 字段，**6 个新字段**（is_main / 互动量子项 / 国家自动识别 / 水军 / KOL 层级 / 关键词命中）在 POC 前 1 周补齐。

### 6 个新字段开发计划

| 字段 | 实现 | 工期 |
|---|---|---|
| is_main（主贴 vs 评论） | Schema 已支持（has parent_id 即评论） | 0.5 天 |
| 互动量子项 | 各平台采集时已采集，需归一化 | 1 天 |
| 国家自动识别 | TLD + 用户地区 + IP | 1 天 |
| 水军识别 | LLM 启发式（账号年龄/评论模式/复制粘贴检测） | 3 天 |
| KOL 层级 | 粉丝数分级（Mega/Macro/Micro/Nano） | 0.5 天 |
| 关键词命中 | 已有，需高亮 | 0.5 天 |

总工期：**1 周**（5 个开发工日）

### 平台适配工期

| 平台 | 工期 | 复用现有 |
|---|---|---|
| Amazon MX/BR/ES | 2 天 | 复用 backend/app/crawlers/amazon.py |
| Lazada ID/TH/MY | 5 天 | 新建 backend/app/crawlers/lazada.py |
| Shopee ID/TH/MY | 5 天 | 新建 backend/app/crawlers/shopee.py |
| Flipkart IN | 3 天 | 新建 backend/app/crawlers/flipkart.py |
| GSMArena | 1 天 | 新建（news/review/opinions） |

总工期：**16 工作日 / 3.5 周**（4 开发并行 → 1 周）

---

## 关于 VIVO 公司 ≥ 100 人门槛

**现实**：smart-crawler 团队 < 10 人，硬性门槛短期解不了。

**3 个绕开方案**：

1. **联合体投标**（最快）：找一家 ≥100 人公司挂主标，smart-crawler 做技术子标
2. **业务并入大公司**：smart-crawler 业务并入一个 ≥100 人母公司（Solvea / 朋友圈大厂）
3. **Q3 完成团队扩张**：8 周从 1-2 人扩到 8-10 人 → Q4 再投（错过 5/6 POC 窗口）

**建议**：先用方案 1 抢 POC 窗口，方案 3 同步推进。

---

## Next Action

1. 用户：确认是否能找到联合体合作方
2. 用户：发邮件给 VIVO 联系人申请 POC 名额
3. 我们：立即开始 Lazada/Shopee/Flipkart crawler 开发（5/6 前 5 周时间）
4. 我们：6 个新字段 schema 落地

---

**预期**：5/6 通过 POC → 6 月签约 → 全年单 ¥50-100w
