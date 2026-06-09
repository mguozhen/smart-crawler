from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_available_percent_parses_meminfo(tmp_path):
    from app import memory_gate

    f = tmp_path / "meminfo"
    f.write_text(
        "MemTotal:       16000000 kB\n"
        "MemFree:         1000000 kB\n"
        "MemAvailable:    4000000 kB\n"
        "Buffers:          200000 kB\n"
    )
    # 4000000 / 16000000 = 25%
    assert memory_gate.available_percent(str(f)) == pytest.approx(25.0)


def test_used_percent_is_complement(tmp_path):
    from app import memory_gate

    f = tmp_path / "meminfo"
    f.write_text("MemTotal: 16000000 kB\nMemAvailable: 4000000 kB\n")
    assert memory_gate.used_percent(str(f)) == pytest.approx(75.0)


def test_available_percent_fail_open_on_missing_file():
    from app import memory_gate

    # 读不到文件 → fail-open 返回 100.0(永不阻塞抓取)
    assert memory_gate.available_percent("/no/such/meminfo") == 100.0


def test_available_percent_fail_open_on_missing_fields(tmp_path):
    from app import memory_gate

    f = tmp_path / "meminfo"
    f.write_text("MemTotal: 16000000 kB\n")   # 缺 MemAvailable
    assert memory_gate.available_percent(str(f)) == 100.0


def test_available_percent_fail_open_on_zero_total(tmp_path):
    from app import memory_gate

    f = tmp_path / "meminfo"
    f.write_text("MemTotal: 0 kB\nMemAvailable: 0 kB\n")
    assert memory_gate.available_percent(str(f)) == 100.0


def test_used_percent_fail_open_on_missing_file():
    from app import memory_gate

    # 读不到文件 → used 视为 0%(fail-open,永不阻塞)
    assert memory_gate.used_percent("/no/such/meminfo") == 0.0
