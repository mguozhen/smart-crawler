#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
APP_SERVICE="${APP_SERVICE:-smart-crawler}"
DEPLOY_BRANCH="${DEPLOY_BRANCH:-}"

echo "== smart-crawler guarded deploy =="
echo "compose: $COMPOSE_FILE"
echo "service: $APP_SERVICE"

scripts/deploy/preflight.sh

BACKUP_LOG="$(mktemp)"
scripts/deploy/backup.sh | tee "$BACKUP_LOG"
BACKUP_DIR="$(awk -F= '/^BACKUP_DIR=/{print $2}' "$BACKUP_LOG" | tail -1)"
rm -f "$BACKUP_LOG"

if [ -n "$DEPLOY_BRANCH" ]; then
  echo "-- updating branch $DEPLOY_BRANCH"
  git fetch origin "$DEPLOY_BRANCH"
  git switch "$DEPLOY_BRANCH"
  git pull --ff-only origin "$DEPLOY_BRANCH"
fi

echo "-- building and starting containers"
docker compose -f "$COMPOSE_FILE" up -d --build

echo "-- applying idempotent DB migration inside app container"
docker compose -f "$COMPOSE_FILE" exec -T "$APP_SERVICE" \
  python scripts/workspace_deploy_guard.py apply --json

echo "-- post-deploy HTTP verification"
scripts/deploy/post_deploy_verify.sh

echo "OK: guarded deploy complete"
echo "Backup kept at: $BACKUP_DIR"
echo "Rollback command:"
echo "  CONFIRM_RESTORE=YES scripts/deploy/restore.sh '$BACKUP_DIR' && docker compose -f '$COMPOSE_FILE' up -d --build"
