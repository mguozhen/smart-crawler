#!/usr/bin/env python3
"""Deployment guard for auth/workspace migrations.

This script is intentionally conservative:
- dry-run only inspects schema/data and reports what init_db() would add.
- apply delegates to the app's idempotent init_db() migration/seed path.
- validate checks the invariants that keep old data and API keys usable.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import inspect, text  # noqa: E402

from app import models  # noqa: F401,E402  register metadata
from app.config import get_sites  # noqa: E402
from app.db import Base, DATABASE_URL, SessionLocal, engine, init_db  # noqa: E402


REQUIRED_TABLES = {
    "workspaces",
    "workspace_members",
    "workspace_sites",
    "report_configs",
    "report_runs",
    "users",
    "user_sessions",
    "invite_codes",
    "api_keys",
    "usage_records",
    "sites",
}

WAREHOUSE_TABLES = [
    "sites",
    "products",
    "categories",
    "promotions",
    "trends",
    "price_history",
    "reviews",
    "shopping_results",
    "keywords",
]


def _database_label() -> str:
    if DATABASE_URL.startswith("sqlite"):
        return DATABASE_URL
    if "@" not in DATABASE_URL:
        return DATABASE_URL
    scheme, rest = DATABASE_URL.split("://", 1)
    return f"{scheme}://***@{rest.split('@', 1)[1]}"


def _inspector():
    return inspect(engine)


def _has_table(insp, table: str) -> bool:
    return insp.has_table(table)


def _columns(insp, table: str) -> set[str]:
    if not _has_table(insp, table):
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def _count(table: str) -> int | None:
    insp = _inspector()
    if not _has_table(insp, table):
        return None
    with engine.connect() as conn:
        return int(conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)


def _scalar(sql: str, params: dict[str, Any] | None = None) -> Any:
    with engine.connect() as conn:
        return conn.execute(text(sql), params or {}).scalar()


def _schema_delta() -> dict[str, Any]:
    insp = _inspector()
    missing_tables: list[str] = []
    missing_columns: dict[str, list[str]] = {}
    for table in Base.metadata.sorted_tables:
        if not _has_table(insp, table.name):
            missing_tables.append(table.name)
            continue
        existing = _columns(insp, table.name)
        missing = [col.name for col in table.columns if col.name not in existing]
        if missing:
            missing_columns[table.name] = missing
    return {"missing_tables": missing_tables, "missing_columns": missing_columns}


def _pre_migration_backfills() -> dict[str, int | None]:
    insp = _inspector()
    result: dict[str, int | None] = {
        "api_keys_without_workspace": None,
        "invite_codes_without_workspace": None,
        "usage_records_without_workspace": None,
        "internal_workspace_sites_to_seed": None,
    }
    if _has_table(insp, "api_keys") and "workspace_id" in _columns(insp, "api_keys"):
        result["api_keys_without_workspace"] = int(_scalar(
            "SELECT COUNT(*) FROM api_keys WHERE workspace_id IS NULL") or 0)
    if _has_table(insp, "invite_codes") and "workspace_id" in _columns(insp, "invite_codes"):
        cols = _columns(insp, "invite_codes")
        if "target_type" in cols:
            result["invite_codes_without_workspace"] = int(_scalar(
                "SELECT COUNT(*) FROM invite_codes "
                "WHERE workspace_id IS NULL "
                "AND COALESCE(target_type, 'workspace') != 'new_workspace'") or 0)
        else:
            result["invite_codes_without_workspace"] = int(_scalar(
                "SELECT COUNT(*) FROM invite_codes WHERE workspace_id IS NULL") or 0)
    if _has_table(insp, "usage_records") and "workspace_id" in _columns(insp, "usage_records"):
        result["usage_records_without_workspace"] = int(_scalar(
            "SELECT COUNT(*) FROM usage_records WHERE workspace_id IS NULL") or 0)
    if _has_table(insp, "workspaces") and _has_table(insp, "workspace_sites") and _has_table(insp, "sites"):
        internal_id = _scalar("SELECT id FROM workspaces WHERE slug = 'internal' LIMIT 1")
        if internal_id is not None:
            site_count = int(_scalar("SELECT COUNT(*) FROM sites") or 0)
            ws_site_count = int(_scalar(
                "SELECT COUNT(*) FROM workspace_sites WHERE workspace_id = :wid",
                {"wid": internal_id},
            ) or 0)
            result["internal_workspace_sites_to_seed"] = max(site_count - ws_site_count, 0)
    return result


def snapshot() -> dict[str, Any]:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "database": _database_label(),
        "git_commit": _git_commit(),
        "schema_delta": _schema_delta(),
        "backfills_needed": _pre_migration_backfills(),
        "table_counts": {table: _count(table) for table in sorted(set(WAREHOUSE_TABLES + list(REQUIRED_TABLES)))},
    }


def _git_commit() -> str | None:
    import subprocess

    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=BACKEND_DIR.parent,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def validate() -> dict[str, Any]:
    insp = _inspector()
    problems: list[str] = []
    warnings: list[str] = []
    delta = _schema_delta()
    missing_required = sorted(REQUIRED_TABLES.intersection(delta["missing_tables"]))
    if missing_required:
        problems.append(f"missing required tables: {', '.join(missing_required)}")
    for table, columns in delta["missing_columns"].items():
        if table in REQUIRED_TABLES and columns:
            problems.append(f"missing columns on {table}: {', '.join(columns)}")

    table_counts = {table: _count(table) for table in sorted(set(WAREHOUSE_TABLES + list(REQUIRED_TABLES)))}
    expected_sites = len(get_sites())

    if _has_table(insp, "workspaces"):
        internal = _scalar("SELECT id FROM workspaces WHERE slug = 'internal' AND status = 'active' LIMIT 1")
        if internal is None:
            problems.append("Internal Workspace is missing or disabled")
        else:
            if _has_table(insp, "workspace_sites") and _has_table(insp, "sites"):
                site_count = int(_scalar("SELECT COUNT(*) FROM sites") or 0)
                ws_site_count = int(_scalar(
                    "SELECT COUNT(*) FROM workspace_sites WHERE workspace_id = :wid",
                    {"wid": internal},
                ) or 0)
                if site_count and ws_site_count < site_count:
                    problems.append(
                        f"Internal Workspace has {ws_site_count}/{site_count} workspace_sites")
    if _has_table(insp, "users"):
        super_admins = int(_scalar(
            "SELECT COUNT(*) FROM users WHERE global_role = 'super_admin' AND status = 'active'") or 0)
        if super_admins < 1:
            problems.append("no active super_admin user")
    if _has_table(insp, "workspace_members"):
        orphan_members = int(_scalar(
            "SELECT COUNT(*) FROM workspace_members m "
            "LEFT JOIN workspaces w ON m.workspace_id = w.id "
            "LEFT JOIN users u ON m.user_id = u.id "
            "WHERE w.id IS NULL OR u.id IS NULL") or 0)
        if orphan_members:
            problems.append(f"workspace_members has {orphan_members} orphan rows")
    if _has_table(insp, "workspace_sites"):
        dup_sites = int(_scalar(
            "SELECT COUNT(*) FROM ("
            "SELECT workspace_id, site, COUNT(*) c FROM workspace_sites "
            "GROUP BY workspace_id, site HAVING COUNT(*) > 1"
            ") t") or 0)
        if dup_sites:
            problems.append(f"workspace_sites has {dup_sites} duplicate workspace/site pairs")
    if _has_table(insp, "api_keys") and "workspace_id" in _columns(insp, "api_keys"):
        null_keys = int(_scalar("SELECT COUNT(*) FROM api_keys WHERE workspace_id IS NULL") or 0)
        invalid_keys = int(_scalar(
            "SELECT COUNT(*) FROM api_keys k LEFT JOIN workspaces w ON k.workspace_id = w.id "
            "WHERE k.workspace_id IS NOT NULL AND w.id IS NULL") or 0)
        if null_keys:
            problems.append(f"api_keys has {null_keys} rows without workspace_id")
        if invalid_keys:
            problems.append(f"api_keys has {invalid_keys} rows pointing to missing workspaces")
    if _has_table(insp, "invite_codes") and "workspace_id" in _columns(insp, "invite_codes"):
        cols = _columns(insp, "invite_codes")
        if "target_type" in cols:
            null_invites = int(_scalar(
                "SELECT COUNT(*) FROM invite_codes "
                "WHERE workspace_id IS NULL "
                "AND COALESCE(target_type, 'workspace') != 'new_workspace'") or 0)
        else:
            null_invites = int(_scalar(
                "SELECT COUNT(*) FROM invite_codes WHERE workspace_id IS NULL") or 0)
        if null_invites:
            problems.append(f"invite_codes has {null_invites} rows without workspace_id")
    if _has_table(insp, "usage_records") and "workspace_id" in _columns(insp, "usage_records"):
        null_usage = int(_scalar("SELECT COUNT(*) FROM usage_records WHERE workspace_id IS NULL") or 0)
        if null_usage:
            problems.append(f"usage_records has {null_usage} rows without workspace_id")
    if table_counts.get("sites") is not None and expected_sites and table_counts["sites"] < expected_sites:
        warnings.append(f"sites table has {table_counts['sites']} rows; sites.yaml has {expected_sites}")

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "database": _database_label(),
        "ok": not problems,
        "problems": problems,
        "warnings": warnings,
        "table_counts": table_counts,
        "expected_sites_from_yaml": expected_sites,
    }


def _print(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description="smart-crawler deployment guard")
    parser.add_argument("command", choices=["snapshot", "dry-run", "apply", "validate"])
    parser.add_argument("--json", action="store_true", help="print JSON output")
    parser.add_argument("--no-validate", action="store_true", help="skip validation after apply")
    args = parser.parse_args()

    if args.command in {"snapshot", "dry-run"}:
        _print(snapshot(), args.json)
        return 0
    if args.command == "apply":
        init_db()
        payload = {"applied": True, "snapshot": snapshot()}
        if not args.no_validate:
            payload["validation"] = validate()
            _print(payload, args.json)
            return 0 if payload["validation"]["ok"] else 1
        _print(payload, args.json)
        return 0
    payload = validate()
    _print(payload, args.json)
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
