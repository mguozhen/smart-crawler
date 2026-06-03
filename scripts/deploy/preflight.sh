#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-backend/.venv/bin/python}"
if [ ! -x "$PYTHON" ]; then
  PYTHON="${PYTHON:-python3}"
fi

if [ "${LOAD_ENV:-1}" = "1" ] && [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

echo "== smart-crawler deploy preflight =="
echo "repo: $ROOT"
echo "commit: $(git rev-parse --short HEAD 2>/dev/null || echo unknown)"

if [ -n "$(git status --porcelain)" ] && [ "${ALLOW_DIRTY:-0}" != "1" ]; then
  echo "FAIL: git working tree is dirty. Commit or set ALLOW_DIRTY=1 for an intentional local deploy." >&2
  git status --short >&2
  exit 1
fi

echo "-- checking required commands"
for cmd in git curl; do
  command -v "$cmd" >/dev/null || { echo "FAIL: missing $cmd" >&2; exit 1; }
done
command -v docker >/dev/null || echo "WARN: docker not found; non-Docker checks will still run"

echo "-- checking production secrets"
if [ "${DEPLOY_ENV:-production}" = "production" ] && [ "${ALLOW_WEAK_SECRETS:-0}" != "1" ]; then
  weak=0
  for name in POSTGRES_PASSWORD SC_SECRET ADMIN_PASSWORD; do
    value="${!name:-}"
    if [ -z "$value" ] || [ "$value" = "change-me" ] || [ "$value" = "changeme" ] || [ "$value" = "change-me-strong-random" ] || [ "${#value}" -lt 12 ]; then
      echo "FAIL: $name must be set to a strong non-default value for production." >&2
      weak=1
    fi
  done
  [ "$weak" = "0" ] || exit 1
fi

echo "-- checking optional LLM configuration"
if [ -n "${ANTHROPIC_API_KEY:-}" ] || [ -n "${OPENAI_API_KEY:-}" ]; then
  if [ "${REQUIRE_LLM:-0}" = "1" ] || [ "${CHECK_LLM_LIVE:-0}" = "1" ]; then
    "$PYTHON" backend/scripts/check_llm_config.py --live
  else
    "$PYTHON" backend/scripts/check_llm_config.py
  fi
else
  if [ "${REQUIRE_LLM:-0}" = "1" ]; then
    echo "FAIL: REQUIRE_LLM=1 but neither ANTHROPIC_API_KEY nor OPENAI_API_KEY is set." >&2
    exit 1
  fi
  echo "WARN: no ANTHROPIC_API_KEY/OPENAI_API_KEY set; LLM-only features will be disabled."
fi

echo "-- scanning committed deployment files for real-looking secrets"
if rg -n "sk-[A-Za-z0-9_-]{32,}|sck_[A-Za-z0-9_-]{24,}|https?://[^[:space:]/:@]+:[^[:space:]@]+@" \
  docker-compose.yml docker-compose.service.yml scripts/deploy scripts/verify_deploy.sh \
  --glob '!backend/tests/influencers/fixtures/**' \
  >/tmp/smart-crawler-secret-scan.txt; then
  cat /tmp/smart-crawler-secret-scan.txt >&2
  echo "FAIL: real-looking secret found in tracked deployment files." >&2
  exit 1
fi

echo "-- database migration dry-run"
"$PYTHON" backend/scripts/workspace_deploy_guard.py dry-run --json

echo "-- compile check"
"$PYTHON" -m compileall backend/app backend/scripts >/tmp/smart-crawler-compileall.log

if [ "${RUN_TESTS:-1}" = "1" ]; then
  echo "-- backend tests"
  (cd backend && .venv/bin/python -m pytest -q)
else
  echo "-- backend tests skipped (RUN_TESTS=0)"
fi

echo "OK: preflight passed"
