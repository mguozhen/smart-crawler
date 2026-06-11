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


from app.db import SessionLocal
from app.spine import get_or_create_dataset, quality_check


def test_get_or_create_dataset_idempotent():
    init_db(); s = SessionLocal()
    d1 = get_or_create_dataset(s, "My Set", workspace_id=None, entity_type="product")
    d2 = get_or_create_dataset(s, "My Set", workspace_id=None, entity_type="product")
    assert d1.id == d2.id and d1.slug == "my-set"
    s.close()


def test_quality_check_promote_if_valid():
    # 高置信 + 必填齐 → main
    st, missing = quality_check({"title": "x"}, "product", 0.9, [], "promote_if_valid")
    assert st == "main" and missing == []
    # 低置信 → staging
    st, _ = quality_check({"title": "x"}, "product", 0.3, [], "promote_if_valid")
    assert st == "staging"
    # 缺必填 → staging + missing
    st, missing = quality_check({}, "product", 0.9, [], "promote_if_valid")
    assert st == "staging" and "title" in missing
    # 显式 main 跳质量门
    st, _ = quality_check({}, "product", 0.1, [], "main")
    assert st == "main"
    # block 警告 → quarantine(覆盖 policy)
    st, _ = quality_check({"title": "x"}, "product", 0.9, ["blocked"], "main")
    assert st == "quarantine"
