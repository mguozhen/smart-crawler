#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

BACKUP_DIR="${1:-}"
if [ -z "$BACKUP_DIR" ] || [ ! -d "$BACKUP_DIR" ]; then
  echo "Usage: CONFIRM_RESTORE=YES $0 /path/to/backup" >&2
  exit 2
fi
if [ "${CONFIRM_RESTORE:-}" != "YES" ]; then
  echo "FAIL: restore is destructive. Re-run with CONFIRM_RESTORE=YES." >&2
  exit 2
fi

if [ "${LOAD_ENV:-1}" = "1" ] && [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

DB_URL="${DATABASE_URL:-sqlite:///$ROOT/data/smart_crawler.db}"
echo "== smart-crawler restore =="
echo "backup: $BACKUP_DIR"

case "$DB_URL" in
  sqlite:///*)
    DB_PATH="${DB_URL#sqlite:///}"
    [ -f "$BACKUP_DIR/smart_crawler.db" ] || { echo "FAIL: backup DB missing" >&2; exit 1; }
    mkdir -p "$(dirname "$DB_PATH")"
    cp "$BACKUP_DIR/smart_crawler.db" "$DB_PATH"
    rm -f "$DB_PATH-wal" "$DB_PATH-shm"
    ;;
  postgresql://*|postgresql+psycopg://*)
    [ -f "$BACKUP_DIR/smart_crawler.pg.dump" ] || { echo "FAIL: backup dump missing" >&2; exit 1; }
    PG_RESTORE_URL="${DB_URL/postgresql+psycopg:/postgresql:}"
    if command -v pg_restore >/dev/null; then
      pg_restore --clean --if-exists --no-owner --no-privileges --dbname="$PG_RESTORE_URL" "$BACKUP_DIR/smart_crawler.pg.dump"
    elif command -v docker >/dev/null; then
      POSTGRES_SERVICE="${POSTGRES_SERVICE:-postgres}"
      PGUSER_IN_CONTAINER="${POSTGRES_USER:-${PGUSER:-smart_crawler}}"
      PGDB_IN_CONTAINER="${POSTGRES_DB:-${PGDATABASE:-smart_crawler}}"
      cat "$BACKUP_DIR/smart_crawler.pg.dump" | docker compose exec -T "$POSTGRES_SERVICE" \
        pg_restore --clean --if-exists --no-owner --no-privileges \
          -U "$PGUSER_IN_CONTAINER" -d "$PGDB_IN_CONTAINER"
    else
      echo "FAIL: pg_restore or docker compose is required for PostgreSQL restore." >&2
      exit 1
    fi
    ;;
  *)
    echo "FAIL: unsupported DATABASE_URL for restore: ${DB_URL%%:*}" >&2
    exit 1
    ;;
esac

if [ "${RESTORE_CONFIG:-0}" = "1" ]; then
  for f in .env docker-compose.yml docker-compose.service.yml backend/sites.yaml backend/proxies.txt; do
    if [ -f "$BACKUP_DIR/$f" ]; then
      mkdir -p "$(dirname "$f")"
      cp "$BACKUP_DIR/$f" "$f"
    fi
  done
  echo "Config files restored from backup"
fi

echo "OK: restore complete"
