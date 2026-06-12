"""超管后台 · spine 管理端点(队列/数据集/计费/健康/审计)。

全部经 _require_super_admin。写操作经 audit.record_audit 埋点。
与现有 routes.py 的 /api/admin/* 并列,不碰它们。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import SpineJob
from .routes import require_user, _require_super_admin

router = APIRouter(prefix="/api/admin/spine", tags=["admin · spine"])

_STUCK_SEC = 600


@router.get("/jobs/stats")
def jobs_stats(user: str = Depends(require_user),
               db: Session = Depends(get_db)) -> dict:
    _require_super_admin(user, db)
    counts = {st: db.query(SpineJob).filter(SpineJob.status == st).count()
              for st in ("pending", "running", "success", "failed")}
    cutoff = datetime.utcnow() - timedelta(seconds=_STUCK_SEC)
    counts["stuck"] = (db.query(SpineJob)
                       .filter(SpineJob.status == "running",
                               SpineJob.heartbeat_at < cutoff).count())
    return counts
