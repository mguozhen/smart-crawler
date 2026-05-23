# 客户 outreach #3 · VIVO 海外 VOC 社媒类舆情年框

> **优先级**：🥉 中（命中度 15% · 投入大）
> **目标**：参与 POC + 中标年单 ¥100-300w
> **决策**：ALL-IN 路线下硬投，需 6-8 周加速开发

---

## 客户画像

- **公司**：VIVO 通讯（同上）
- **场景**：海外 5 大社媒平台 + 14 国 14 品牌 + ASR + 多语 NLP
- **量级**：1 亿条/年（按条计费，初步预估）
- **POC 账号清单**：367 个账号 URL（IG 90 + YT 89 + FB 77 + TikTok 58 + X 52）
- **国家**：印度、印尼、巴西、菲律宾、泰国、马来西亚、巴基斯坦、孟加拉、沙特、墨西哥、俄罗斯、哥伦比亚、西班牙、意大利等
- **品牌**：vivo / iQOO / OPPO / 三星 / 小米 / Apple / 华为 / 荣耀 / Realme / 一加 / 传音 / Nothing Phone / Google Pixel / Motorola
- **特殊要求**：图文 + 音视频 ASR 转文本、水军/广告识别、国家识别、互动量按天更新、近 2 年历史回溯

---

## 邮件模板

```
主题：VIVO 海外 VOC 社媒年框 · smart-crawler 应答（5 平台 + ASR）

VIVO [负责人] 您好，

我是 boyuan@solvea.cx，smart-crawler 创始人。我们也参与 VIVO 海外 VOC 社媒类舆情监测项目 POC。这是个高难度项目（5 平台 + ASR + 多语 + 367 账号），我们的方案：

▎ smart-crawler 5 平台覆盖现状

| 平台 | 现状 | 6 周内 |
|---|---|---|
| X / Twitter | 通过 d60/twikit 已可采（无 API key） | 生产化 |
| Instagram | instaloader + instagrapi 双备份 | 生产化 |
| YouTube | YouTube Data API + ASR pipeline | 生产化 |
| TikTok | Evil0ctal/Douyin_TikTok_Download_API | 生产化 |
| Facebook | 走 mobile 端协议 + Playwright | 6 周从 0 建 |

▎ ASR pipeline
• Whisper Large v3 自部署（音频 → 文本，支持 100+ 语种）
• AssemblyAI 备份（高峰期）
• 视频先做关键帧采样 → 增量转文本（成本可控）

▎ 多语种 NLP（不翻译）
• 14 国 = 至少 12 种语言（hi/id/pt/tl/th/ms/ur/bn/ar/es/ru/it）
• 用 claude-haiku-4-5 + LLM gateway，原文情感分析

▎ 关于 100 人门槛
团队当前 < 100。建议方案：
1. 联合体投标（找 ≥100 人 SI 公司挂主标）
2. smart-crawler 做技术子标，承担 5 平台 + ASR + NLP 核心
3. 主标公司负责合规 + 客服 + 服务流程

▎ 报价（POC 后细化）
• 按 record 计费：$0.5 / 1k records 起
• 年单：1 亿条 × $0.0005 = **¥50w-100w 基础**，含 ASR + NLP 增值至 ¥150-300w

约 30min 电话讨论是否能加入 POC？

Best，
Boyuan Yuan
smart-crawler 创始人
+86 [手机]
boyuan@solvea.cx
```

---

## 技术应答方案

### 5 平台 fetcher 矩阵

| 平台 | 主路径 | 备份路径 | 反爬难度 |
|---|---|---|---|
| **X (Twitter)** | `d60/twikit`（4.4k⭐） | 官方 v2 API（rate limit 严） | 🟡 中 |
| **Instagram** | `instaloader`（12.4k⭐） | `instagrapi` private API | 🔴 高 |
| **YouTube** | YouTube Data API v3 | yt-dlp + Playwright | 🟢 低 |
| **TikTok** | `Evil0ctal/Douyin_TikTok_Download_API` | Playwright + StealthyFetcher | 🟡 中 |
| **Facebook** | 自建 mobile 协议 + Playwright | 第三方 SaaS (Apify) | 🔴 极高 |

### ASR pipeline 架构

```
Video URL → ffmpeg 提取音轨 → Whisper Large v3（本地 GPU）
                              ↓
                          回退 AssemblyAI（云）
                              ↓
                         分段文本 + 时间戳
                              ↓
                    多语种识别（langdetect）
                              ↓
                          原文情感分析
```

**成本估算**：
- Whisper 自部署：~$0.001/min（GPU 折算）
- 1 亿条/年（假设 30% 含视频，平均 60s/视频）→ 3000 万 min × $0.001 = **$30k/年 ASR 成本**
- 加 NLP 调用 + 存储：年成本 ~$80-120k

### 367 账号实时监测

- 主贴 24h 内 ≥ 90% 覆盖率
- 评论 48h 内 ≥ 80% 覆盖率
- 主贴下评论连续监测 10 天
- 全部异步队列 + 重试 + 漏采修复

### 28 字段输出（同 VIVO 电商，社媒版）

含特殊字段：
- KOL 层级（粉丝数 + 互动率分级）
- 水军识别（账号年龄 + 评论模式 + 复制粘贴检测）
- 国家识别（用户地区 + IP + 语言）
- 互动量子项（likes/comments/shares/views/saves/repins）

---

## 6 周加速开发计划

| 周 | 任务 |
|---|---|
| W1 | X + Instagram + YouTube 三平台 fetcher 生产化 |
| W2 | TikTok + Facebook 两平台从 0 建 |
| W3 | ASR pipeline（Whisper 部署 + 测试 100h 样本） |
| W4 | 多语种 NLP fine-tune + 14 国识别 |
| W5 | 367 账号实时监测调度 + alerting |
| W6 | 28 字段 schema 输出 + API 文档 + POC 测试 |

**需要资源**：3-4 个开发并行（社媒 2 + ASR/NLP 1 + 调度 1）

---

## Risk + Mitigation

| Risk | Mitigation |
|---|---|
| 反爬触发 IP 封禁 | 10 商业代理池 + 错峰调度 + 退避策略 |
| 1 亿条/年 存储压力 | PG 迁移 + 分区 + 冷热分层 |
| 多语种 NLP 准确率 | LLM + 人工质检（每月 1000 样本） |
| ASR 成本失控 | 增量转文本（只转关键 KOL 视频） |
| Facebook 反爬最严 | 早期试水 + 高难度站不承诺 SLA |

---

## Next Action

1. 用户：发邮件给 VIVO 联系人申请 POC 名额（强调联合体方案）
2. 用户：找 ≥100 人 SI 公司谈联合投标
3. 我们：立即启动 W1 任务（X/IG/YT fetcher 生产化）
4. 我们：申请 GPU 资源（用于 Whisper ASR）

---

**预期**：6-8 周完成开发 → 7-8 月 POC → 9 月签约 → 全年单 ¥100-300w
