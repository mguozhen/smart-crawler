"""后台管理系统(admin spine)测试。"""
from app.db import SessionLocal, init_db


def test_admin_audit_log_table_and_record():
    init_db()
    from sqlalchemy import inspect
    from app.db import engine
    cols = {c["name"] for c in inspect(engine).get_columns("admin_audit_logs")}
    for c in ("id", "actor_user_id", "actor_name", "action", "target_type",
              "target_id", "detail", "ip", "created_at"):
        assert c in cols, f"admin_audit_logs 缺列 {c}"
    from app.audit import record_audit
    from app.models import AdminAuditLog
    s = SessionLocal()
    n0 = s.query(AdminAuditLog).count()
    record_audit(s, actor_user_id=1, actor_name="admin", action="test.action",
                 target_type="job", target_id="42", detail={"k": "v"}, ip="1.2.3.4")
    s.commit()
    n1 = s.query(AdminAuditLog).count()
    assert n1 == n0 + 1
    row = s.query(AdminAuditLog).order_by(AdminAuditLog.id.desc()).first()
    assert row.action == "test.action" and row.target_id == "42"
    assert row.detail == {"k": "v"} and row.actor_name == "admin"
    s.close()


def test_jobs_stats_requires_super_admin():
    from fastapi.testclient import TestClient
    from app.main import app
    init_db()
    client = TestClient(app)
    r = client.get("/api/admin/spine/jobs/stats")
    assert r.status_code in (401, 403)


def test_jobs_stats_ok_for_admin():
    init_db()
    from app.api.admin_spine import jobs_stats
    from app.db import SessionLocal
    s = SessionLocal()
    out = jobs_stats(user="admin", db=s)
    s.close()
    for k in ("pending", "running", "success", "failed", "stuck"):
        assert k in out
