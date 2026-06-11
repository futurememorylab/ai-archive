#!/bin/sh
# Container entrypoint. onetun is NO LONGER started here — the app owns it
# via VpnSupervisor (default off; toggle in the UI). See ADR 0075.
#   litestream -- SQLite restore + replication (when LITESTREAM_REPLICA_URL set)
#   uvicorn    -- the app (always; exec'd so it receives SIGTERM)
set -eu

PORT="${PORT:-8765}"
UVICORN="python -m uvicorn backend.app.main:app --host 0.0.0.0 --port $PORT --timeout-graceful-shutdown 3"

if [ -n "${LITESTREAM_REPLICA_URL:-}" ]; then
  litestream restore -if-db-not-exists -if-replica-exists "$DB_PATH"
  exec litestream replicate -exec "$UVICORN"
fi

exec $UVICORN
