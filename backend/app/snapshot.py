"""原始采集快照归档 —— 把每次采集的原始响应压缩存档到大盘。

价值：数据出错可回溯重解析、竞品纠纷留证据、采集器升级后对历史快照重跑提字段。
落盘路径：SNAPSHOT_DIR/{site}/{YYYY-MM-DD}/{name}.gz
"""
from __future__ import annotations

import gzip
import os
from datetime import date
from pathlib import Path

_DEFAULT = Path(__file__).resolve().parent.parent.parent / "data" / "snapshots"
SNAPSHOT_DIR = Path(os.environ.get("SNAPSHOT_DIR", str(_DEFAULT)))
ENABLED = os.environ.get("SNAPSHOT_ENABLED", "1") != "0"


def _safe(name: str) -> str:
    cleaned = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in str(name))
    return cleaned[:120] or "snapshot"


def save(site: str, name: str, content) -> None:
    """归档一份原始响应。content 为 str 或 bytes；失败静默（不影响采集主流程）。"""
    if not ENABLED or content is None:
        return
    try:
        day = date.today().isoformat()
        folder = SNAPSHOT_DIR / site / day
        folder.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8") if isinstance(content, str) else content
        with gzip.open(folder / f"{_safe(name)}.gz", "wb") as f:
            f.write(data)
    except Exception:
        pass


def stats() -> dict:
    """快照归档统计（用于运维 / 看板）。"""
    if not SNAPSHOT_DIR.exists():
        return {"sites": 0, "files": 0, "bytes": 0}
    files = list(SNAPSHOT_DIR.rglob("*.gz"))
    return {
        "sites": len([p for p in SNAPSHOT_DIR.iterdir() if p.is_dir()]),
        "files": len(files),
        "bytes": sum(p.stat().st_size for p in files),
    }
