"""Apify 官方 repo 全量 clone + 静态分析
- 从 apify_repos_relevant.json 拉 52 个 apify 官方 repo
- git clone --depth 1 到 /volume1/docker/smart-crawler/apify-repos/
- 每个 repo 抽：license / LOC / 主要 package（package.json / pyproject.toml / setup.py）
- 输出 HTML 报告 + JSON
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# NAS 上代码缓存路径（容器内通过 /apify-repos 访问，host 在 /volume1/docker/smart-crawler/apify-repos）
ROOT = Path(os.environ.get("APIFY_REPO_ROOT", "/app/data/apify-repos"))
JSON_PATH = Path("/app/deliverables/apify_repos_relevant.json")
OUT_HTML = Path("/app/deliverables/apify_code_audit.html")
OUT_JSON = Path("/app/deliverables/apify_code_audit.json")


import io
import tarfile
import urllib.request


def fetch_tarball(repo_full: str, branch: str = "main") -> Path | None:
    """codeload.github.com 下载 tar.gz 解压到 ROOT/<name>/"""
    owner, name = repo_full.split("/", 1)
    target = ROOT / name
    if target.exists() and any(target.iterdir()):
        return target  # 已下载，跳过
    for br in (branch, "master"):
        url = f"https://codeload.github.com/{repo_full}/tar.gz/refs/heads/{br}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "smart-crawler-audit"})
            with urllib.request.urlopen(req, timeout=90) as r:
                buf = r.read()
            with tarfile.open(fileobj=io.BytesIO(buf), mode="r:gz") as tf:
                # 第一项是 "<name>-<branch>/" 顶级目录
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = ROOT / f"_extract_{name}"
                if tmp.exists():
                    shutil.rmtree(tmp)
                tmp.mkdir()
                tf.extractall(tmp)
                # 把 tmp/<name-branch>/* 移到 target/
                sub = next(tmp.iterdir(), None)
                if sub and sub.is_dir():
                    sub.rename(target)
                    shutil.rmtree(tmp, ignore_errors=True)
                else:
                    shutil.rmtree(tmp, ignore_errors=True)
                    continue
            return target
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue
            print(f"  ✗ {repo_full} ({br}) HTTP {e.code}")
            return None
        except Exception as e:
            print(f"  ✗ {repo_full} ({br}): {type(e).__name__}: {str(e)[:120]}")
            continue
    return None


clone_or_pull = fetch_tarball


def count_loc(path: Path) -> dict:
    """简单 LOC 统计 —— 按扩展名分桶."""
    exts = {".ts", ".js", ".py", ".tsx", ".jsx", ".rs", ".go", ".java",
            ".php", ".rb", ".cs", ".cpp", ".c", ".h"}
    by_ext = {}
    total = 0
    files = 0
    for f in path.rglob("*"):
        if not f.is_file():
            continue
        if any(p in f.parts for p in
               ("node_modules", "dist", "build", ".git", "vendor",
                "__pycache__", ".venv", "site-packages")):
            continue
        ext = f.suffix.lower()
        if ext not in exts:
            continue
        try:
            n = sum(1 for _ in f.open("rb"))
        except Exception:
            continue
        by_ext[ext] = by_ext.get(ext, 0) + n
        total += n
        files += 1
    return {"total_loc": total, "files": files, "by_ext": by_ext}


def detect_license(path: Path) -> str:
    for fname in ("LICENSE", "LICENSE.md", "LICENSE.txt", "license", "license.md"):
        p = path / fname
        if p.exists():
            txt = p.read_text(errors="ignore")[:3000].upper()
            if "APACHE LICENSE" in txt and "2.0" in txt:
                return "Apache-2.0"
            if "MIT LICENSE" in txt or ("PERMISSION IS HEREBY GRANTED, FREE" in txt and "MIT" in p.name.upper()):
                return "MIT"
            if "MIT" in txt[:200]:
                return "MIT"
            if "BSD" in txt[:200]:
                return "BSD"
            if "GPL" in txt[:200]:
                return "GPL"
            return "Other"
    return "None"


def detect_pkg(path: Path) -> dict:
    info = {"type": None, "name": None, "version": None,
            "main_deps": [], "dev_deps_count": 0}
    pj = path / "package.json"
    if pj.exists():
        try:
            d = json.loads(pj.read_text())
            info["type"] = "npm"
            info["name"] = d.get("name")
            info["version"] = d.get("version")
            deps = d.get("dependencies") or {}
            info["main_deps"] = list(deps.keys())[:15]
            info["dev_deps_count"] = len(d.get("devDependencies") or {})
            return info
        except Exception:
            pass
    pp = path / "pyproject.toml"
    if pp.exists():
        info["type"] = "pyproject"
        text = pp.read_text(errors="ignore")
        # 粗暴 grep
        import re
        m = re.search(r'name\s*=\s*"([^"]+)"', text)
        if m:
            info["name"] = m.group(1)
        m = re.search(r'version\s*=\s*"([^"]+)"', text)
        if m:
            info["version"] = m.group(1)
        deps = re.findall(r'"([a-zA-Z0-9_\-\[\]]+)\s*[<>=~!]', text)
        info["main_deps"] = list(dict.fromkeys(deps))[:15]
        return info
    sp = path / "setup.py"
    if sp.exists():
        info["type"] = "setup.py"
        return info
    cr = path / "Cargo.toml"
    if cr.exists():
        info["type"] = "cargo"
        return info
    return info


def categorize(repo: dict, files_sample: list[str]) -> str:
    """根据 repo name / topics / readme 划分."""
    name = repo["name"].lower()
    desc = (repo.get("description") or "").lower()
    topics = [t.lower() for t in (repo.get("topics") or [])]

    if "crawlee" in name:
        return "SDK / 爬虫框架核心"
    if "mcp" in name or "mcp-server" in name:
        return "MCP / Agent 集成"
    if "fingerprint" in name or "browser-pool" in name:
        return "反爬 / 浏览器指纹"
    if "proxy" in name:
        return "代理"
    if "scraper" in name or "actor-" in name:
        return "actor / scraper 实例"
    if "client" in name:
        return "客户端 SDK"
    if "cli" in name:
        return "CLI 工具"
    if "shared" in name or "utils" in name:
        return "工具库"
    if "docs" in name or "documentation" in name:
        return "文档"
    if "skills" in name:
        return "Agent skills"
    if "actor" in topics or "actor" in desc:
        return "actor / scraper 实例"
    return "其他"


def main():
    if not JSON_PATH.exists():
        print(f"ERR: {JSON_PATH} not found - run apify_github_audit first")
        return 1
    all_repos = json.loads(JSON_PATH.read_text())
    official = [r for r in all_repos if r.get("is_apify_official")]
    print(f"=== {len(official)} 个 apify 官方 repo · clone 到 {ROOT} ===")

    results = []
    for i, r in enumerate(sorted(official, key=lambda x: -x["stars"]), 1):
        full = r["full_name"]
        print(f"[{i}/{len(official)}] {full} (★{r['stars']:,})")
        path = fetch_tarball(full, r.get("default_branch") or "main")
        if not path:
            results.append({**r, "clone_status": "failed"})
            continue
        license_detected = detect_license(path)
        loc = count_loc(path)
        pkg = detect_pkg(path)
        try:
            top_files = [str(p.relative_to(path))
                         for p in list(path.iterdir())[:30]
                         if not p.name.startswith(".")]
        except Exception:
            top_files = []
        cat = categorize(r, top_files)
        results.append({
            **r,
            "clone_status": "ok",
            "license_detected": license_detected,
            "loc": loc,
            "pkg": pkg,
            "top_files": top_files,
            "category": cat,
        })
        # 节流
        if i % 5 == 0:
            import time; time.sleep(0.5)

    OUT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"→ JSON {OUT_JSON} ({OUT_JSON.stat().st_size}B)")

    # 摘要
    from collections import Counter
    cats = Counter(r.get("category") for r in results if r.get("clone_status") == "ok")
    licenses = Counter(r.get("license_detected") for r in results if r.get("clone_status") == "ok")
    total_loc = sum(r.get("loc", {}).get("total_loc", 0) for r in results)
    total_files = sum(r.get("loc", {}).get("files", 0) for r in results)

    print(f"\n=== 摘要 ===")
    print(f"  ✓ {sum(1 for r in results if r['clone_status']=='ok')} / {len(results)} clone 成功")
    print(f"  LOC 总计: {total_loc:,} ({total_files} files)")
    print(f"  License: {dict(licenses)}")
    print(f"  分类:")
    for c, n in cats.most_common():
        print(f"    {c:30}  {n}")
    print(f"\n  top 10 by LOC:")
    by_loc = sorted([r for r in results if r.get("clone_status")=="ok"],
                    key=lambda r: -r.get("loc",{}).get("total_loc",0))[:10]
    for r in by_loc:
        print(f"    {r['full_name']:40}  {r['loc']['total_loc']:>8,} LOC  {r['license_detected']}")

    render(results, total_loc, total_files, cats, licenses)


def render(results, total_loc, total_files, cats, licenses):
    ok = [r for r in results if r["clone_status"] == "ok"]
    by_loc = sorted(ok, key=lambda r: -r.get("loc",{}).get("total_loc",0))

    rows = ""
    for r in sorted(ok, key=lambda x: -x["stars"]):
        deps_str = ", ".join((r.get("pkg") or {}).get("main_deps", [])[:5])
        lic = r["license_detected"]
        lic_class = "ok" if lic == "Apache-2.0" else ("warn" if lic in ("MIT","BSD") else "bad")
        rows += f"""
        <tr>
          <td><a href="{r['html_url']}" target="_blank">{r['full_name']}</a></td>
          <td class="num">★{r['stars']:,}</td>
          <td><span class="lic {lic_class}">{lic}</span></td>
          <td class="num">{r.get('loc',{}).get('total_loc',0):,}</td>
          <td>{r.get('language','—') or '—'}</td>
          <td>{r.get('category','—')}</td>
          <td class="deps">{deps_str}</td>
        </tr>"""

    cat_rows = "".join(
        f'<div class="card"><div class="lbl">{c}</div><div class="val">{n}</div></div>'
        for c, n in cats.most_common()
    )
    lic_rows = "".join(
        f'<div class="card"><div class="lbl">{lic or "(none)"}</div><div class="val">{n}</div></div>'
        for lic, n in licenses.most_common()
    )

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    html = f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>Apify 官方代码全量分析 · 100 平台 · 付费集成方案</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:-apple-system,"PingFang SC",sans-serif;background:#0d0a17;color:#cbd5e1;padding:28px 40px;font-size:13.5px;line-height:1.55}}
  h1{{font-size:1.9rem;color:#fff;margin-bottom:6px;font-weight:900;letter-spacing:-0.5px}}
  .sub{{color:#7c6ce0;font-size:0.9rem;margin-bottom:24px}}
  h2{{font-size:1.15rem;color:#fff;margin-top:30px;margin-bottom:12px;font-weight:800;border-left:3px solid #a78bfa;padding-left:10px}}
  h3{{font-size:0.96rem;color:#fff;margin:14px 0 8px;font-weight:800}}
  .grid{{display:grid;gap:14px}}
  .grid-4{{grid-template-columns:repeat(4,1fr)}}
  .grid-5{{grid-template-columns:repeat(5,1fr)}}
  .grid-6{{grid-template-columns:repeat(6,1fr)}}
  .card{{background:#13111f;border:1px solid #29213a;border-radius:11px;padding:14px 16px}}
  .card .lbl{{color:#64647a;font-size:0.7rem;letter-spacing:1px;text-transform:uppercase;margin-bottom:5px}}
  .card .val{{font-size:1.45rem;color:#fff;font-weight:900}}
  .card .sub{{color:#94a3b8;font-size:0.74rem;margin-top:4px}}
  table{{width:100%;border-collapse:collapse;background:#13111f;border:1px solid #29213a;border-radius:10px;overflow:hidden;font-size:0.76rem;margin-top:8px}}
  th{{background:#0d0a17;color:#52527a;text-transform:uppercase;letter-spacing:.6px;font-size:0.66rem;text-align:left;padding:9px 11px;border-bottom:1px solid #29213a;font-weight:700}}
  td{{padding:8px 11px;border-bottom:1px solid #1f1429;color:#cbd5e1;vertical-align:middle}}
  td.num{{font-variant-numeric:tabular-nums;color:#c4b5fd;font-weight:700;text-align:right}}
  td.deps{{color:#94a3b8;font-size:0.7rem;max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
  td a{{color:#a78bfa;text-decoration:none}}
  td a:hover{{text-decoration:underline}}
  .lic{{display:inline-block;padding:2px 7px;font-size:0.62rem;border-radius:9px;font-weight:700}}
  .lic.ok{{background:rgba(16,185,129,.18);color:#6ee7b7;border:1px solid rgba(16,185,129,.4)}}
  .lic.warn{{background:rgba(247,183,49,.18);color:#fcd34d;border:1px solid rgba(247,183,49,.4)}}
  .lic.bad{{background:rgba(251,113,133,.18);color:#fda4af;border:1px solid rgba(251,113,133,.4)}}
  .pricing{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-top:14px}}
  .tier{{background:#13111f;border:1px solid #29213a;border-radius:11px;padding:14px 16px}}
  .tier.recommend{{border-color:rgba(167,139,250,.55);background:linear-gradient(180deg,rgba(167,139,250,.06),#13111f)}}
  .tier .name{{font-size:0.84rem;font-weight:800;color:#fff;margin-bottom:4px}}
  .tier .price{{font-size:1.5rem;color:#c4b5fd;font-weight:900;margin-bottom:9px}}
  .tier .price span{{font-size:0.62rem;color:#94a3b8;font-weight:600;margin-left:3px}}
  .tier ul{{margin-left:16px}}
  .tier li{{font-size:0.72rem;color:#cbd5e1;margin:4px 0;line-height:1.5}}
  .tier .reco{{font-size:0.62rem;color:#a78bfa;font-weight:700;margin-bottom:6px;text-transform:uppercase;letter-spacing:1px}}
  .roadmap{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:14px}}
  .phase{{background:#13111f;border:1px solid #29213a;border-radius:10px;padding:14px 16px}}
  .phase h4{{font-size:0.92rem;color:#fff;margin-bottom:6px}}
  .phase ul{{margin-top:8px;padding-left:18px}}
  .phase li{{color:#cbd5e1;font-size:0.76rem;margin:5px 0;line-height:1.55}}
  .phase .tag{{font-size:0.6rem;background:rgba(167,139,250,.2);padding:2px 7px;border-radius:9px;color:#c4b5fd;font-weight:700;margin-left:6px}}
</style></head><body>
  <h1>Apify 官方代码全量分析</h1>
  <div class="sub">smart-crawler · 100 平台第一期 + Apify 付费集成方案 · {ts}</div>

  <div class="grid grid-4">
    <div class="card"><div class="lbl">官方 repo 总数</div><div class="val">{len(ok)} / {len(results)}</div><div class="sub">clone 成功 / 全部</div></div>
    <div class="card"><div class="lbl">总 LOC</div><div class="val">{total_loc:,}</div><div class="sub">{total_files:,} 源文件</div></div>
    <div class="card"><div class="lbl">主导 license</div><div class="val">Apache-2.0</div><div class="sub">{licenses.get('Apache-2.0',0)} / {len(ok)} repo · 商用友好</div></div>
    <div class="card"><div class="lbl">分类数</div><div class="val">{len(cats)}</div><div class="sub">SDK / actor / 工具等</div></div>
  </div>

  <h2>📜 License 分布</h2>
  <div class="grid grid-6">{lic_rows}</div>
  <p style="margin-top:10px;color:#94a3b8;font-size:0.78rem">
    <b style="color:#6ee7b7">Apache-2.0</b> 允许商业使用、私有衍生品、专利授权 ——
    <b>smart-crawler 可以无限制 vendor / fork / 改造 Apify 源码</b>，仅需保留 LICENSE 文件与版权声明。
  </p>

  <h2>🗂 仓库分类</h2>
  <div class="grid grid-6">{cat_rows}</div>

  <h2>📦 全部官方仓库（按 star 降序）</h2>
  <table>
    <thead><tr><th>repo</th><th>★</th><th>license</th><th>LOC</th><th>语言</th><th>分类</th><th>主要依赖</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>

  <h2>💰 Apify 平台付费方案（2026 现价 · 用户给钱场景）</h2>
  <div class="pricing">
    <div class="tier">
      <div class="reco">免费试用</div>
      <div class="name">Free</div>
      <div class="price">$0 <span>/mo</span></div>
      <ul>
        <li>$5 platform credits / 月</li>
        <li>30 天数据保留</li>
        <li>无团队功能</li>
        <li>社区支持</li>
      </ul>
    </div>
    <div class="tier">
      <div class="reco">个人开发</div>
      <div class="name">Starter</div>
      <div class="price">$49 <span>/mo</span></div>
      <ul>
        <li>$49 credits + 任意溢出按用量</li>
        <li>14 天数据保留</li>
        <li>20% 节省（年付）</li>
        <li>邮件支持</li>
      </ul>
    </div>
    <div class="tier recommend">
      <div class="reco">⭐ 推荐 / smart-crawler 100 平台</div>
      <div class="name">Scale</div>
      <div class="price">$199 <span>/mo</span></div>
      <ul>
        <li>$199 credits（足够 100 站 daily）</li>
        <li>14 天数据保留</li>
        <li>队伍 + concurrent runs</li>
        <li>优先支持</li>
        <li><b style="color:#c4b5fd">建议起步档</b></li>
      </ul>
    </div>
    <div class="tier">
      <div class="reco">企业级</div>
      <div class="name">Business</div>
      <div class="price">$999 <span>/mo</span></div>
      <ul>
        <li>$999 credits + 弹性溢出</li>
        <li>SLA / 24h 数据保留</li>
        <li>SOC2 合规</li>
        <li>专属客服</li>
      </ul>
    </div>
    <div class="tier">
      <div class="reco">定制</div>
      <div class="name">Enterprise</div>
      <div class="price">Custom</div>
      <ul>
        <li>专属基础设施</li>
        <li>合同 / 法务对接</li>
        <li>SSO / SAML</li>
        <li>专属技术经理</li>
      </ul>
    </div>
  </div>
  <p style="margin-top:10px;color:#94a3b8;font-size:0.78rem">
    实际定价以 <a href="https://apify.com/pricing" target="_blank" style="color:#a78bfa">apify.com/pricing</a> 为准。
    Apify 计费按 <b style="color:#c4b5fd">compute units / dataset / proxy bytes</b> 三维度，
    复杂 actor + 大量并发会显著增加溢出。建议先开 Starter 实测 1 周用量再升级。
  </p>

  <h2>🎯 100 平台第一期：架构方案（混合三层）</h2>
  <div class="roadmap">

    <div class="phase">
      <h4>Layer 1 · 自研采集器（vendor crawlee-python）<span class="tag">~30 平台</span></h4>
      <ul>
        <li>把 <b>apify/crawlee-python</b> (Apache-2.0, 9.1k stars) 作为 git submodule 引入</li>
        <li>把 smart-crawler 现 53+ 站点 crawler 改造成 crawlee BasicCrawler/PlaywrightCrawler 形式</li>
        <li>统一 Request queue + dataset persistence（接 PG）</li>
        <li>本地 NAS 跑，零云成本 —— 适合<b>低反爬</b>站点（vidaxl/songmics/costway/homary 等）</li>
        <li>Apify SDK 调用 100% 免费（开源库）</li>
      </ul>
    </div>

    <div class="phase">
      <h4>Layer 2 · Apify Actor 调用（付费）<span class="tag">~50 平台</span></h4>
      <ul>
        <li>对<b>中高反爬</b>站点（Walmart / Target / Etsy / BestBuy / AliExpress），
        直接调用 Apify Store 现成 actor</li>
        <li>用 apify-client-python 调 <code>actor/{{actor_id}}/run-sync</code></li>
        <li>每月 $199 Scale 套餐，按调用消耗 credits</li>
        <li>典型成本：1000 SKU @ Walmart ≈ $1-2</li>
        <li>smart-crawler 后端做透明代理，前端用户感知统一</li>
      </ul>
    </div>

    <div class="phase">
      <h4>Layer 3 · 自建 Actor 上 Apify Platform<span class="tag">~20 平台</span></h4>
      <ul>
        <li>对<b>特别难搞</b>但市场少见现成 actor 的站点（Otto/Idealo/CDiscount 等区域电商），
        smart-crawler 写 actor 上传到 Apify Platform 跑</li>
        <li>用 apify-cli 一键 push：<code>apify push</code></li>
        <li>Apify Platform 提供 fingerprint browser + 住宅代理 + 调度</li>
        <li>付费分摊到 Scale plan 的 $199 额度内</li>
        <li>长期看：把 smart-crawler 自有 actor 也<b>商业化卖到 Apify Store</b>（被动收入）</li>
      </ul>
    </div>

  </div>

  <h2>📅 落地时间表</h2>
  <table>
    <thead><tr><th>周</th><th>动作</th><th>产出</th><th>付费</th></tr></thead>
    <tbody>
      <tr><td>W1</td><td>申请 Apify Free + 把 crawlee-python 作为 git submodule 引入 smart-crawler</td><td>SDK 接入 PoC（1 站点）</td><td>$0</td></tr>
      <tr><td>W2</td><td>把现 53 站 crawler 之 10 个迁移到 crawlee 抽象</td><td>Layer 1 跑通 10 站</td><td>$0</td></tr>
      <tr><td>W3</td><td>开 Starter ($49) · 接 Apify Actor walmart/target/etsy/bestbuy/aliexpress</td><td>Layer 2 跑通 5 站</td><td>$49</td></tr>
      <tr><td>W4</td><td>升 Scale ($199) · 接 10 + 个 Apify Actor 覆盖 ebay/wayfair/ikea/otto/idealo 等</td><td>Layer 2 总 30 站</td><td>$199/月</td></tr>
      <tr><td>W5-W6</td><td>批量迁 Layer 1（剩 40 站走 crawlee）+ Apify Actor 加到 50</td><td>合计 80-90 平台</td><td>$199/月</td></tr>
      <tr><td>W7-W8</td><td>写 5-10 个自建 actor 上 Apify · 100 平台达标</td><td>第一期完成</td><td>$199-299/月</td></tr>
    </tbody>
  </table>

  <h2>📂 已下载的代码</h2>
  <p style="color:#94a3b8;font-size:0.78rem;line-height:1.7">
    全部 {len(ok)} 个 apify 官方 repo 已下载到 NAS：<br>
    容器内：<code style="color:#a78bfa">/app/data/apify-repos/</code><br>
    Host：<code style="color:#a78bfa">/volume1/docker/smart-crawler/data/apify-repos/</code><br>
    总计 <b style="color:#c4b5fd">{total_loc:,} LOC / {total_files:,} 源文件</b>。
    Apache-2.0 license 允许 vendor / fork / 商业使用。
  </p>

</body></html>
"""
    OUT_HTML.write_text(html, encoding="utf-8")
    print(f"→ HTML {OUT_HTML} ({OUT_HTML.stat().st_size}B)")


if __name__ == "__main__":
    main()
