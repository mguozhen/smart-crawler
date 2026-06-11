"""通用数据脊柱（SP1）测试。"""
from sqlalchemy import inspect

from app.db import engine, init_db


def test_spine_tables_exist():
    init_db()
    insp = inspect(engine)
    for t in ("raw_snapshots", "extracted_records", "datasets"):
        assert insp.has_table(t), f"缺表 {t}"
    cols = {c["name"] for c in insp.get_columns("extracted_records")}
    for c in ("dataset_id", "snapshot_id", "source_url", "canonical_url",
              "entity_type", "data", "record_key", "content_hash",
              "confidence", "extraction_method", "recipe_id",
              "quality_status", "fetched_at", "workspace_id"):
        assert c in cols, f"extracted_records 缺列 {c}"


from app.spine import canonical_url, content_hash


def test_canonical_strips_tracking_and_normalizes():
    a = canonical_url("https://Shop.Example.com/p/1?utm_source=x&id=5")
    b = canonical_url("https://shop.example.com/p/1/?id=5&fbclid=z")
    assert a == b  # 跟踪参去掉、host 小写、末尾斜杠统一、保留 id
    assert "utm_source" not in a and "fbclid" not in a


def test_canonical_prefers_explicit():
    got = canonical_url("https://x.com/a?utm_source=q",
                        explicit="https://x.com/canonical")
    assert got == "https://x.com/canonical"


def test_content_hash_stable_and_order_independent():
    assert content_hash({"a": 1, "b": 2}) == content_hash({"b": 2, "a": 1})
    assert content_hash({"a": 1}) != content_hash({"a": 2})
