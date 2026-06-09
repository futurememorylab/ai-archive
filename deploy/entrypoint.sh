#!/bin/sh
# Container entrypoint. Up to three processes, each gated by env:
#   onetun     -- userspace WireGuard to CatDV (when WG_PRIVATE_KEY is set)
#   litestream -- SQLite restore + replication (when LITESTREAM_REPLICA_URL is set)
#   uvicorn    -- the app (always; exec'd so it receives SIGTERM)
#
# Shutdown chain (Cloud Run SIGTERM, 10s grace): litestream forwards to
# uvicorn -> lifespan aclose() releases the CatDV seat (logout bounded
# to 3s) -> litestream final WAL sync -> exit. onetun just dies with
# the container; its death at runtime only degrades CatDV to offline.
set -eu

PORT="${PORT:-8765}"
UVICORN="python -m uvicorn backend.app.main:app --host 0.0.0.0 --port $PORT --timeout-graceful-shutdown 3"

if [ -n "${WG_PRIVATE_KEY:-}" ]; then
  ( while true; do
      onetun --private-key "$WG_PRIVATE_KEY" \
             --endpoint-addr "$WG_ENDPOINT" \
             --endpoint-public-key "$WG_PEER_PUBKEY" \
             --source-peer-ip "$WG_SOURCE_IP" \
             --keep-alive 25 \
             127.0.0.1:18080:192.168.1.41:8080:TCP || true
      echo "onetun exited; restarting in 2s" >&2
      sleep 2
    done ) &
fi

if [ -n "${LITESTREAM_REPLICA_URL:-}" ]; then
  litestream restore -if-db-not-exists -if-replica-exists "$DB_PATH"
  exec litestream replicate -exec "$UVICORN"
fi

exec $UVICORN
