"""复用 apify_repos.json，加 relevance 过滤后重渲染 audit
relevant = owner=='apify' OR name 含 'apify' OR description 含 'apify' OR topics 含 'apify'
排除掉只在 readme 一笔带过的 noise（如 firecrawl / awesome-mcp-servers 之类）
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/app/backend/scripts")
from apify_github_audit import analyze, render_html  # noqa: E402

JSON_PATH = Path("/app/deliverables/apify_repos.json")
ALL_HTML = Path("/app/deliverables/apify_audit_all.html")
RELEVANT_HTML = Path("/app/deliverables/apify_audit.html")
RELEVANT_JSON = Path("/app/deliverables/apify_repos_relevant.json")


def is_relevant(r: dict) -> bool:
    if r.get("is_apify_official"):
        return True
    name = (r.get("name") or "").lower()
    desc = (r.get("description") or "").lower()
    topics = [t.lower() for t in (r.get("topics") or [])]
    if "apify" in name:
        return True
    if "apify" in topics:
        return True
    # description 必须明确提 apify（避免只在 readme 被关键词扫到）
    if "apify" in desc and not any(
        noise in desc for noise in ["awesome", "list of", "curated"]
    ):
        return True
    return False


def main():
    all_repos = json.loads(JSON_PATH.read_text())
    print(f"all = {len(all_repos)}")

    relevant = [r for r in all_repos if is_relevant(r)]
    print(f"relevant = {len(relevant)} (filtered out {len(all_repos)-len(relevant)} noise)")

    RELEVANT_JSON.write_text(json.dumps(relevant, ensure_ascii=False, indent=2))
    print(f"  → {RELEVANT_JSON} ({RELEVANT_JSON.stat().st_size}B)")

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    # 全量保留一份（供查漏）
    ALL_HTML.write_text(render_html(analyze(all_repos), ts + " · 全量含噪音"))
    print(f"  → {ALL_HTML} ({ALL_HTML.stat().st_size}B)")

    # 主报告 = 仅 relevant
    RELEVANT_HTML.write_text(render_html(analyze(relevant), ts))
    print(f"  → {RELEVANT_HTML} ({RELEVANT_HTML.stat().st_size}B)")

    # 摘要
    a = analyze(relevant)
    print(f"\n=== relevant 摘要 ===")
    print(f"  总数: {a['total']}")
    print(f"  官方/社区: {dict(a['official_vs_community'])}")
    print(f"  语言 top5: {a['by_language'][:5]}")
    print(f"  Top 10 by stars:")
    for r in a["top_50"][:10]:
        print(f"    ★{r['stars']:>6,}  {r['full_name']:42}  {r['language']}")


if __name__ == "__main__":
    main()
