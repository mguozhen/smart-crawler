#!/usr/bin/env bash
# Backward-compatible entrypoint for deployment verification.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

exec scripts/deploy/post_deploy_verify.sh
