#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "Creating .venv..."
  python3 -m venv .venv
fi

.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e ".[dev]"

if [ ! -f .env ]; then
  echo "ERROR: .env missing. Copy from .env.example and edit." >&2
  exit 1
fi

if [ -n "${CATDV_HEALTH_CHECK:-}" ]; then
  HOST=$(grep -E '^CATDV_BASE_URL=' .env | cut -d= -f2 | sed 's|http://||' | cut -d: -f1)
  if ! ping -c1 -W1 "$HOST" >/dev/null 2>&1; then
    echo "WARN: $HOST not reachable. Is the VPN up?" >&2
  fi
fi

UVICORN_ARGS=()
if [ "${DEV_RELOAD:-0}" = "1" ]; then
  UVICORN_ARGS+=(--reload --reload-dir backend)
fi

exec .venv/bin/uvicorn backend.app.main:app \
  --host "$(grep -E '^BIND_HOST=' .env | cut -d= -f2)" \
  --port "$(grep -E '^BIND_PORT=' .env | cut -d= -f2)" \
  "${UVICORN_ARGS[@]}"
