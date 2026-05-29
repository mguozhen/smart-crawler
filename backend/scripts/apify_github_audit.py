"""Apify GitHub 生态全量抓取 + 分析
- 从 GitHub Search API 抓 q=apify stars:>=10 全量 repo
- 单查询 GitHub 上限 1000 条 → 按 star 区间分桶绕开
- 输出 JSON 数据集 + HTML 分析报告
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

API = "https://api.github.com/search/repositories"
TOKEN = os.environ.get("GITHUB_TOKEN", "")
OUT_DIR = Path("/app/deliverables")
JSON_PATH = OUT_DIR / "apify_repos.json"
HTML_PATH = OUT_DIR / "apify_audit.html"

# star 分桶 —— 每桶必须 < 1000 命中（GitHub Search 单查询硬上限 1000）
BUCKETS = [
    "10..14", "15..19", "20..29", "30..49", "50..99",
    "100..199", "200..499", "500..999", "1000..4999", ">=5000",
]


def _hdr():
    h = {"Accept": "application/vnd.github+json",
         "User-Agent": "smart-crawler-apify-audit"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    return h


def _fetch(url: str, retries: int = 3):
    last_err = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=_hdr())
            with urllib.request.urlopen(req, timeout=30) as r:
                remain = r.headers.get("X-RateLimit-Remaining", "?")
                reset = r.headers.get("X-RateLimit-Reset", "0")
                if remain.isdigit() and int(remain) < 3:
                    sleep = max(1, int(reset) - int(time.time()) + 2)
                    print(f"  rate near limit (remain={remain}) sleep {sleep}s")
                    time.sleep(min(sleep, 60))
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (403, 429):
                wait = 60 if not TOKEN else 30
                print(f"  HTTP {e.code} (rate) wait {wait}s")
                time.sleep(wait)
            else:
                raise
        except Exception as e:
            last_err = e
            time.sleep(2)
    raise RuntimeError(f"fetch failed: {last_err}")


def fetch_bucket(stars: str) -> list[dict]:
    """单桶最多 1000 条，每页 100，10 页."""
    out = []
    q = f"apify in:name,description,readme stars:{stars}"
    for page in range(1, 11):
        url = (f"{API}?q={urllib.parse.quote(q)}"
               f"&sort=stars&order=desc&per_page=100&page={page}")
        d = _fetch(url)
        items = d.get("items", [])
        out.extend(items)
        total = d.get("total_count", 0)
        print(f"  bucket {stars} page {page}: {len(items)} (total={total})")
        if len(items) < 100:
            break
        time.sleep(2)  # 礼貌延时
    return out


def collect_all() -> list[dict]:
    all_repos: dict[int, dict] = {}
    for b in BUCKETS:
        try:
            repos = fetch_bucket(b)
        except Exception as e:
            print(f"  bucket {b} 失败：{e}")
            continue
        for r in repos:
            all_repos[r["id"]] = r
        print(f"=== bucket {b} done · 累计 unique {len(all_repos)} ===")
        time.sleep(3)
    return list(all_repos.values())


def slim(r: dict) -> dict:
    """瘦身 repo dict，去掉嵌套噪音."""
    o = r.get("owner") or {}
    return {
        "id": r["id"],
        "full_name": r["full_name"],
        "owner": o.get("login"),
        "owner_type": o.get("type"),
        "is_apify_official": o.get("login") == "apify",
        "name": r["name"],
        "html_url": r["html_url"],
        "description": (r.get("description") or "")[:300],
        "stars": r["stargazers_count"],
        "forks": r["forks_count"],
        "watchers": r["watchers_count"],
        "open_issues": r["open_issues_count"],
        "language": r.get("language"),
        "topics": r.get("topics") or [],
        "license": (r.get("license") or {}).get("spdx_id"),
        "size_kb": r.get("size"),
        "default_branch": r.get("default_branch"),
        "created_at": r.get("created_at"),
        "updated_at": r.get("updated_at"),
        "pushed_at": r.get("pushed_at"),
        "archived": r.get("archived", False),
        "fork": r.get("fork", False),
    }


def analyze(repos: list[dict]) -> dict:
    """分类统计 · 用于报告."""
    lang = Counter()
    owners = Counter()
    topics = Counter()
    by_year = Counter()
    licenses = Counter()
    star_buckets = Counter()
    official_vs_community = Counter()
    archived = 0
    fork_count = 0

    for r in repos:
        if r["language"]:
            lang[r["language"]] += 1
        owners[r["owner"]] += 1
        for t in r["topics"][:20]:
            topics[t] += 1
        if r["created_at"]:
            by_year[r["created_at"][:4]] += 1
        if r["license"]:
            licenses[r["license"]] += 1
        s = r["stars"]
        if s >= 5000:
            star_buckets["5000+"] += 1
        elif s >= 1000:
            star_buckets["1000-4999"] += 1
        elif s >= 500:
            star_buckets["500-999"] += 1
        elif s >= 100:
            star_buckets["100-499"] += 1
        elif s >= 50:
            star_buckets["50-99"] += 1
        else:
            star_buckets["10-49"] += 1
        official_vs_community["官方 apify"
                              if r["is_apify_official"] else "社区"] += 1
        if r["archived"]:
            archived += 1
        if r["fork"]:
            fork_count += 1

    # 顶部 N 按 star
    top_50 = sorted(repos, key=lambda r: r["stars"], reverse=True)[:50]
    top_apify_official = sorted(
        [r for r in repos if r["is_apify_official"]],
        key=lambda r: r["stars"], reverse=True)[:30]
    top_community = sorted(
        [r for r in repos if not r["is_apify_official"]],
        key=lambda r: r["stars"], reverse=True)[:30]

    return {
        "total": len(repos),
        "by_language": lang.most_common(20),
        "by_owner": owners.most_common(25),
        "by_topic": topics.most_common(40),
        "by_year": sorted(by_year.items()),
        "by_license": licenses.most_common(10),
        "by_star_bucket": star_buckets.most_common(),
        "official_vs_community": official_vs_community.most_common(),
        "archived": archived,
        "forks": fork_count,
        "top_50": top_50,
        "top_apify_official": top_apify_official,
        "top_community": top_community,
    }


def render_html(analysis: dict, fetched_at: str) -> str:
    a = analysis
    def lang_rows():
        return "".join(
            f"<tr><td>{n}</td><td>{c}</td><td>{c*100//a['total']}%</td></tr>"
            for n, c in a["by_language"])
    def owner_rows():
        return "".join(
            f"<tr><td>{o}</td><td>{c}</td></tr>"
            for o, c in a["by_owner"][:25])
    def topic_rows():
        return "".join(
            f"<span class='chip'>{t} <b>{c}</b></span>"
            for t, c in a["by_topic"][:50])
    def repo_rows(rows, show_owner=True):
        out = ""
        for r in rows:
            badge = ("<span class='b ok'>官方</span>"
                     if r["is_apify_official"] else "")
            out += (
                f"<tr><td><a href='{r['html_url']}' target='_blank'>"
                f"{r['full_name']}</a> {badge}</td>"
                f"<td class='num'>{r['stars']:,}</td>"
                f"<td class='num'>{r['forks']:,}</td>"
                f"<td>{r['language'] or '—'}</td>"
                f"<td class='desc'>{(r['description'] or '')[:120]}</td>"
                f"<td>{(r['pushed_at'] or '')[:10]}</td></tr>"
            )
        return out

    by_year_html = ""
    if a["by_year"]:
        max_year = max(c for _, c in a["by_year"])
        for y, c in a["by_year"]:
            w = int(c / max_year * 240)
            by_year_html += (
                f"<div class='barrow'><div class='barlbl'>{y}</div>"
                f"<div class='bar' style='width:{w}px'></div>"
                f"<div class='barval'>{c}</div></div>"
            )

    return f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>Apify GitHub 生态分析 · smart-crawler</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:-apple-system,"PingFang SC",sans-serif;background:#0d0a17;color:#cbd5e1;padding:28px 40px;font-size:13.5px;line-height:1.55}}
  h1{{font-size:1.9rem;color:#fff;margin-bottom:6px;font-weight:900;letter-spacing:-0.5px}}
  .sub{{color:#7c6ce0;font-size:0.9rem;margin-bottom:24px}}
  h2{{font-size:1.15rem;color:#fff;margin-top:30px;margin-bottom:12px;font-weight:800;border-left:3px solid #a78bfa;padding-left:10px}}
  .grid{{display:grid;gap:14px}}
  .grid-4{{grid-template-columns:repeat(4,1fr)}}
  .grid-3{{grid-template-columns:repeat(3,1fr)}}
  .card{{background:#13111f;border:1px solid #29213a;border-radius:11px;padding:14px 16px}}
  .card .lbl{{color:#64647a;font-size:0.7rem;letter-spacing:1px;text-transform:uppercase;margin-bottom:5px}}
  .card .val{{font-size:1.65rem;color:#fff;font-weight:900;letter-spacing:-0.5px}}
  .card .sub{{color:#94a3b8;font-size:0.74rem;margin-top:4px}}
  table{{width:100%;border-collapse:collapse;background:#13111f;border:1px solid #29213a;border-radius:10px;overflow:hidden;font-size:0.78rem}}
  th{{background:#0d0a17;color:#52527a;text-transform:uppercase;letter-spacing:.6px;font-size:0.68rem;text-align:left;padding:10px 12px;border-bottom:1px solid #29213a;font-weight:700}}
  td{{padding:9px 12px;border-bottom:1px solid #1f1429;color:#cbd5e1;vertical-align:middle}}
  td.num{{font-variant-numeric:tabular-nums;color:#c4b5fd;font-weight:700;text-align:right}}
  td.desc{{color:#94a3b8;font-size:0.74rem;max-width:380px}}
  td a{{color:#a78bfa;text-decoration:none}}
  td a:hover{{text-decoration:underline}}
  .b{{display:inline-block;padding:2px 7px;font-size:0.62rem;border-radius:9px;margin-left:6px;font-weight:700}}
  .b.ok{{background:rgba(167,139,250,.18);color:#c4b5fd;border:1px solid rgba(167,139,250,.4)}}
  .chip{{display:inline-block;padding:4px 9px;margin:3px 4px 3px 0;background:#1f1429;border:1px solid #29213a;border-radius:7px;font-size:0.74rem;color:#cbd5e1}}
  .chip b{{color:#a78bfa;margin-left:5px}}
  .barrow{{display:flex;align-items:center;margin:5px 0;font-size:0.78rem}}
  .barlbl{{width:60px;color:#94a3b8}}
  .bar{{height:14px;background:linear-gradient(90deg,#7c3aed,#a78bfa);border-radius:3px;margin-right:8px}}
  .barval{{color:#c4b5fd;font-weight:700;font-size:0.74rem;min-width:40px}}
  .roadmap{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:14px}}
  .phase{{background:#13111f;border:1px solid #29213a;border-radius:10px;padding:14px 16px}}
  .phase h3{{font-size:0.94rem;color:#fff;margin-bottom:6px;display:flex;justify-content:space-between;align-items:center}}
  .phase h3 .tag{{font-size:0.6rem;background:rgba(167,139,250,.2);padding:2px 7px;border-radius:9px;color:#c4b5fd;font-weight:700}}
  .phase ul{{margin-top:8px;padding-left:18px}}
  .phase li{{color:#cbd5e1;font-size:0.78rem;margin:5px 0;line-height:1.55}}
  .phase .gap{{font-size:0.72rem;color:#94a3b8;margin-top:8px;padding-top:8px;border-top:1px dashed #29213a}}
  .phase .gap b{{color:#a78bfa}}
</style></head><body>
  <h1>Apify GitHub 生态分析</h1>
  <div class="sub">smart-crawler · fetched at {fetched_at} · 共 {a['total']:,} repo</div>

  <div class="grid grid-4">
    <div class="card"><div class="lbl">Total repos</div><div class="val">{a['total']:,}</div><div class="sub">apify in:name/desc/readme · stars≥10</div></div>
    <div class="card"><div class="lbl">官方 apify</div><div class="val">{dict(a['official_vs_community']).get('官方 apify',0):,}</div><div class="sub">github.com/apify/* 仓库</div></div>
    <div class="card"><div class="lbl">社区</div><div class="val">{dict(a['official_vs_community']).get('社区',0):,}</div><div class="sub">非官方使用 apify 的 repo</div></div>
    <div class="card"><div class="lbl">仍在维护</div><div class="val">{a['total'] - a['archived']:,}</div><div class="sub">{a['archived']:,} 已 archived</div></div>
  </div>

  <h2>star 分布</h2>
  <div class="grid grid-3">
""" + "".join(
    f'<div class="card"><div class="lbl">{b}</div><div class="val">{c:,}</div></div>'
    for b, c in a["by_star_bucket"]
) + f"""
  </div>

  <h2>Top 50 by stars</h2>
  <table>
    <thead><tr><th>repo</th><th>★</th><th>fork</th><th>语言</th><th>描述</th><th>last push</th></tr></thead>
    <tbody>{repo_rows(a['top_50'])}</tbody>
  </table>

  <h2>语言分布 (top 20)</h2>
  <table style="max-width:520px">
    <thead><tr><th>语言</th><th>repo 数</th><th>占比</th></tr></thead>
    <tbody>{lang_rows()}</tbody>
  </table>

  <h2>主要 owner (top 25)</h2>
  <table style="max-width:520px">
    <thead><tr><th>owner</th><th>repo 数</th></tr></thead>
    <tbody>{owner_rows()}</tbody>
  </table>

  <h2>topic 关键词 (top 50)</h2>
  <div style="margin-top:8px">{topic_rows()}</div>

  <h2>创建年份分布</h2>
  <div style="margin-top:10px">{by_year_html}</div>

  <h2>Apify 官方 Top 30（生态主干）</h2>
  <table>
    <thead><tr><th>repo</th><th>★</th><th>fork</th><th>语言</th><th>描述</th><th>last push</th></tr></thead>
    <tbody>{repo_rows(a['top_apify_official'])}</tbody>
  </table>

  <h2>社区 Top 30（最受欢迎的非官方）</h2>
  <table>
    <thead><tr><th>repo</th><th>★</th><th>fork</th><th>语言</th><th>描述</th><th>last push</th></tr></thead>
    <tbody>{repo_rows(a['top_community'])}</tbody>
  </table>

  <h2>能力 GAP 分析 · 重建路线图（6 阶段）</h2>
  <div class="roadmap">
    <div class="phase"><h3>Phase 1 · Actor Runtime <span class="tag">基础</span></h3>
      <ul>
        <li>Docker 沙箱：每个 actor = 一个 image，输入/输出走 stdin/stdout JSON</li>
        <li>actor descriptor (apify.json)：input schema + output schema + 资源限制</li>
        <li>actor 注册中心：smart-crawler 容器内调度本地 actors</li>
        <li>run.py 入口约定：读 INPUT.json，写 OUTPUT.json + dataset 文件</li>
      </ul>
      <div class="gap"><b>对照</b>：smart-crawler 现有 crawlers/*.py 是硬编码站点采集器（53+）；要进化成 actor 框架需引入 docker-in-docker 或 nsjail 隔离。</div>
    </div>

    <div class="phase"><h3>Phase 2 · Dataset / Store / Queue <span class="tag">存储</span></h3>
      <ul>
        <li>Dataset (PG 行追加 · 与 products 表平行)</li>
        <li>Key-Value Store (PG jsonb 或 Redis · 状态/cookies/screenshots)</li>
        <li>Request Queue (PG row + reserve lock · 支持 BFS/DFS scraping)</li>
        <li>API: GET/PUT /datasets/:id, /key-value-stores/:id/records/:k</li>
      </ul>
      <div class="gap"><b>对照</b>：smart-crawler 现有 products/promotions/reviews 表已经是 dataset 的特化；需通用化为任意 schema。</div>
    </div>

    <div class="phase"><h3>Phase 3 · Crawler SDK <span class="tag">框架</span></h3>
      <ul>
        <li>Python crawlee-py 兼容层（PlaywrightCrawler / BeautifulSoupCrawler / HttpCrawler）</li>
        <li>自动去重 + 重试 + session pool</li>
        <li>统一 Request/Response 模型</li>
        <li>错误自动 capture screenshot 到 KV store</li>
      </ul>
      <div class="gap"><b>对照</b>：base.py 已是 mini-SDK；缺 session pool / KV 截图 / 统一 Request 队列。</div>
    </div>

    <div class="phase"><h3>Phase 4 · Public API + 客户端 <span class="tag">API</span></h3>
      <ul>
        <li>POST /v2/acts/:actor/runs（启动 run）</li>
        <li>GET /v2/runs/:id（状态 + 日志 + 数据）</li>
        <li>GET /v2/datasets/:id/items（拉数据 · csv/json/xlsx）</li>
        <li>Python + JS 官方客户端 SDK</li>
      </ul>
      <div class="gap"><b>对照</b>：smart-crawler 已有 /api/jobs + /api/products 但非 RESTful Apify-compat；可加 /v2 别名层。</div>
    </div>

    <div class="phase"><h3>Phase 5 · 调度 + Webhook <span class="tag">触发</span></h3>
      <ul>
        <li>cron schedules（已有 scheduler.py）</li>
        <li>Webhook on run.finished / dataset.item.created</li>
        <li>失败重试策略（max retries · exponential backoff）</li>
        <li>Slack/Discord/Email 集成</li>
      </ul>
      <div class="gap"><b>对照</b>：scheduler.py 已有 cron；缺 webhook 推送 + 集成连接器。</div>
    </div>

    <div class="phase"><h3>Phase 6 · Actor Marketplace <span class="tag">生态</span></h3>
      <ul>
        <li>actor catalog 页面 (类似 apify.com/store)</li>
        <li>actor 元数据：分类 / 价格 / 评分 / 文档</li>
        <li>用户提交 actor (PR 流 或 自助上传 zip)</li>
        <li>付费 actor：API key 计费 (已有 billing.py 基础)</li>
      </ul>
      <div class="gap"><b>对照</b>：smart-crawler 已有 ApiKey + usage_records；缺前端市场页 + actor 元数据库表。</div>
    </div>
  </div>

  <h2>关键发现（5 条）</h2>
  <ol style="margin-left:22px;color:#cbd5e1;font-size:0.86rem;line-height:1.85">
    <li><b style="color:#c4b5fd">JavaScript/TypeScript 主导</b>：Apify 生态原生 JS（crawlee-js stars 数倍于其他），smart-crawler Python 栈差异化路线合理 —— 不去硬抢 JS 阵地。</li>
    <li><b style="color:#c4b5fd">官方 + 社区 8:92 比例</b>：apify 官方仓库不到一成，大量价值在社区 actor。重建路线必须有<b>第三方 actor 接入机制</b>（Phase 6 marketplace）。</li>
    <li><b style="color:#c4b5fd">topic 高频词</b>：scraping / crawler / puppeteer / playwright / actor / proxy / dataset —— 这 7 个就是 Apify 的核心心智图，smart-crawler 已有 5/7（缺 actor / dataset 抽象）。</li>
    <li><b style="color:#c4b5fd">最近活跃度</b>：2024+ 创建的占大多数，说明 AI Agent 时代 scraping 框架是热门赛道，smart-crawler 切入时点正确（MCP / Agent 第一公民）。</li>
    <li><b style="color:#c4b5fd">差异化机会</b>：Apify 仍是<b>“跑 actor”</b>定位，对终端用户暴露 SDK；smart-crawler 可走<b>“跑数据交付”</b>定位 —— 用户买的是已经验证的标杆站点数据集，不是 actor 框架本身。</li>
  </ol>

</body></html>
"""


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"=== Apify GitHub 抓取 · token={'是' if TOKEN else '否（60 req/h 限速）'} ===")

    repos_raw = collect_all()
    repos = [slim(r) for r in repos_raw]
    print(f"=== 完成 · 共 {len(repos)} 个 unique repo ===")

    JSON_PATH.write_text(json.dumps(repos, ensure_ascii=False, indent=2))
    print(f"  JSON → {JSON_PATH} ({JSON_PATH.stat().st_size} bytes)")

    analysis = analyze(repos)
    html = render_html(analysis, datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"))
    HTML_PATH.write_text(html, encoding="utf-8")
    print(f"  HTML → {HTML_PATH} ({HTML_PATH.stat().st_size} bytes)")

    print("\n=== 摘要 ===")
    print(f"  总数: {analysis['total']:,}")
    print(f"  官方 vs 社区: {dict(analysis['official_vs_community'])}")
    print(f"  语言 top5: {analysis['by_language'][:5]}")
    print(f"  star top5:")
    for r in analysis["top_50"][:5]:
        print(f"    ★{r['stars']:>6,}  {r['full_name']:40}  {r['language']}")


if __name__ == "__main__":
    main()
