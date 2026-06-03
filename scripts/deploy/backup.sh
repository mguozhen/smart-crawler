#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

if [ "${LOAD_ENV:-1}" = "1" ] && [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

TS="$(date +%Y%m%d_%H%M%S)"
BACKUP_ROOT="${BACKUP_ROOT:-$ROOT/backups/deploy}"
BACKUP_DIR="${BACKUP_DIR:-$BACKUP_ROOT/$TS}"
PYTHON="${PYTHON:-backend/.venv/bin/python}"
if [ ! -x "$PYTHON" ]; then
  PYTHON=python3
fi

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

echo "== smart-crawler backup =="
echo "BACKUP_DIR=$BACKUP_DIR"

git rev-parse HEAD > "$BACKUP_DIR/git_commit.txt" 2>/dev/null || true
git status --short > "$BACKUP_DIR/git_status.txt" 2>/dev/null || true

for f in .env docker-compose.yml docker-compose.service.yml backend/sites.yaml backend/proxies.txt; do
  if [ -f "$f" ]; then
    mkdir -p "$BACKUP_DIR/$(dirname "$f")"
    cp "$f" "$BACKUP_DIR/$f"
  fi
done

"$PYTHON" backend/scripts/workspace_deploy_guard.py snapshot --json > "$BACKUP_DIR/pre_migration_snapshot.json" || true

DB_URL="${DATABASE_URL:-sqlite:///$ROOT/data/smart_crawler.db}"
case "$DB_URL" in
  sqlite:///*)
    DB_PATH="${DB_URL#sqlite:///}"
    if [ ! -f "$DB_PATH" ]; then
      echo "WARN: SQLite DB not found: $DB_PATH"
    elif command -v sqlite3 >/dev/null; then
      sqlite3 "$DB_PATH" ".backup '$BACKUP_DIR/smart_crawler.db'"
      echo "SQLite backup: $BACKUP_DIR/smart_crawler.db"
    else
      cp "$DB_PATH" "$BACKUP_DIR/smart_crawler.db"
      [ -f "$DB_PATH-wal" ] && cp "$DB_PATH-wal" "$BACKUP_DIR/smart_crawler.db-wal"
      [ -f "$DB_PATH-shm" ] && cp "$DB_PATH-shm" "$BACKUP_DIR/smart_crawler.db-shm"
      echo "SQLite file copy backup: $BACKUP_DIR/smart_crawler.db"
    fi
    ;;
  postgresql://*|postgresql+psycopg://*)
    PG_DUMP_URL="${DB_URL/postgresql+psycopg:/postgresql:}"
    if command -v pg_dump >/dev/null; then
      pg_dump --format=custom --no-owner --no-privileges "$PG_DUMP_URL" > "$BACKUP_DIR/smart_crawler.pg.dump"
    elif command -v docker >/dev/null; then
      POSTGRES_SERVICE="${POSTGRES_SERVICE:-postgres}"
      PGUSER_IN_CONTAINER="${POSTGRES_USER:-${PGUSER:-smart_crawler}}"
      PGDB_IN_CONTAINER="${POSTGRES_DB:-${PGDATABASE:-smart_crawler}}"
      docker compose exec -T "$POSTGRES_SERVICE" \
        pg_dump --format=custom --no-owner --no-privileges \
          -U "$PGUSER_IN_CONTAINER" "$PGDB_IN_CONTAINER" > "$BACKUP_DIR/smart_crawler.pg.dump"
    else
      echo "FAIL: pg_dump or docker compose is required for PostgreSQL backups." >&2
      exit 1
    fi
    echo "PostgreSQL backup: $BACKUP_DIR/smart_crawler.pg.dump"
    ;;
  *)
    echo "FAIL: unsupported DATABASE_URL for backup: ${DB_URL%%:*}" >&2
    exit 1
    ;;
esac

echo "OK: backup complete"
