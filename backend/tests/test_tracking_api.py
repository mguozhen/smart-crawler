"""标杆追踪面板 API + 迁移测试。"""
from sqlalchemy import inspect

from app.db import engine, init_db


def test_site_has_tracking_columns():
    init_db()
    cols = {c["name"] for c in inspect(engine).get_columns("sites")}
    for col in ("track_status", "source", "creator", "review_rate",
                "created_at", "updated_at"):
        assert col in cols, f"sites 缺列 {col}"
