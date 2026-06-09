"""内存自适应并发闸 —— 读主机内存,提供"等到内存 OK"的阻塞原语。

零依赖(读 Linux /proc/meminfo,不引入 psutil)。fail-open:读不到内存时
返回 100% available,闸永不阻塞抓取。容器未设 per-container 内存限制,
故以主机级 MemAvailable 为信号(OOM 风险是主机级)。

详见 docs/superpowers/specs/2026-06-09-memory-adaptive-concurrency-gate-design.md
"""
from __future__ import annotations

import time


def _read_meminfo(path: str) -> dict[str, int]:
    """解析 /proc/meminfo 为 {key: kB}。读不到 → 空 dict。"""
    out: dict[str, int] = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                parts = line.split(":")
                if len(parts) != 2:
                    continue
                key = parts[0].strip()
                val = parts[1].strip().split()
                # isdigit() 排除负数 —— /proc/meminfo 数值恒 ≥ 0
                if val and val[0].isdigit():
                    out[key] = int(val[0])
    except OSError:
        return {}
    return out


def available_percent(meminfo_path: str = "/proc/meminfo") -> float:
    """可用内存百分比 = MemAvailable / MemTotal * 100。
    读不到 / 缺字段 / 非 Linux → 返回 100.0(fail-open,永不阻塞)。"""
    info = _read_meminfo(meminfo_path)
    total = info.get("MemTotal")
    avail = info.get("MemAvailable")
    if not total or avail is None:
        return 100.0
    return avail / total * 100.0


def used_percent(meminfo_path: str = "/proc/meminfo") -> float:
    """已用内存百分比 = 100 - available_percent()。"""
    return 100.0 - available_percent(meminfo_path)
