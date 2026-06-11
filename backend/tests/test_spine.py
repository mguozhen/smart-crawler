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
