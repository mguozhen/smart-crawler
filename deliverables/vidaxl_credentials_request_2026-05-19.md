# vidaxl 数据接入凭据需求

> 2026-05-19 · smart-crawler 项目 · 转给客户

## TL;DR

smart-crawler 已覆盖 vidaxl 全球 12 个独立站中的 10 站（uk / ie / de / it / es / fr / nl / pt / ro / pl）。
剩下 **vidaxl_us** 和 **vidaxl_ca** 两站不接受常规爬虫访问，需要客户协助提供以下任意一种凭据。

## 卡点根因（实测）

| 站点 | 现象 | 根因 |
|---|---|---|
| `vidaxl_us` | HTTPS 401 Unauthorized | Demandware Basic Auth 墙 |
| `vidaxl_ca` | sitemap_index.xml 返回 200 但 body 是空 `<sitemapindex/>`；主页是 SPA 骨架屏，商品 JS 异步加载；跨站 URL 复用测试 0/5 成功 | Demandware SPA 客户端渲染，无可用的纯 HTML 爬取入口 |

我们已尝试：备用 sitemap 路径（全部 404）、跨站 URL 复用（500/404）、Demandware 标准 search 端点（返回骨架屏 0 商品链接）。常规爬虫路径不可行。

## 所需凭据（任选其一）

### 方案 A（推荐）—— Dropshipping API

注册 vidaxl B2B Dropshipper 账号（免费），登录后台拿到：

- `VIDAXL_API_EMAIL` —— 注册邮箱
- `VIDAXL_API_TOKEN` —— 后台生成的 API token

**一份凭据通杀全部 vidaxl 站点**（含 us / ca / 已通的 10 个欧洲站），并且能拿到完整商品库（含库存、B2B 价、零售建议价、EAN/GTIN 等字段，比爬虫拿到的更全）。

我们的代码已就绪：写入 NAS 的 `.env` 后重启容器即可，无须改代码。

注册入口：<https://b2b.vidaxl.com/>

### 方案 B —— 美国站住宅代理

仅解决 `vidaxl_us`：提供一个干净的美国住宅 IP 代理（SOCKS5 或 HTTPS proxy）。不解决 ca。

成本：住宅代理服务约 $50-200/月。

### 方案 C —— 放弃 us + ca

若两个市场对客户优先级低，标记为"已知不支持站点"，按当前 43/46 站点交付。

## 影响范围

| 选项 | 站点覆盖 | 商品规模估计 | 周期 |
|---|---|---|---|
| A | 46/46 ✅ | +400 (us+ca, 凭 vidaxl 全球 ~200K SKU 估算) | 1-2 天（拿到凭据后） |
| B | 44/46 | +200 (仅 us) | 立即（拿到代理后） |
| C | 43/46（当前） | — | 0 |

## 当前规避措施（已上线）

在 vidaxl_ca / vidaxl_us 的采集逻辑里加了显式失败标志，避免之前的 "status=success but 0 products" silent fail，监控看板能清楚识别该站点处于 "需凭据" 状态。

---

**联系**：smartcrawler.io · 接口人 Bo Yuan (boyuan@solvea.cx)
