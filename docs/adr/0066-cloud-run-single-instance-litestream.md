# 0066. Cloud Run pinned to one instance; SQLite persisted via Litestream

**Date:** 2026-06-09
**Status:** Accepted
**Lifespan:** Invariant

## Context

Deploying to Cloud Run (spec 2026-06-09-cloud-run-deployment-design)
raises two state questions: SQLite durability on an ephemeral
filesystem, and horizontal scaling. Three constraints align: CatDV has
effectively one free session seat; Litestream supports exactly one
writer per replica path; the in-process write queue serializes writes
only within one process.

## Alternatives

- Cloud SQL (Postgres): correct for multi-instance, but a full
  migration (dialect, repos, write queue removal) with no current need.
- SQLite on a GCS FUSE mount: object storage lacks SQLite's locking
  semantics; corruption risk. Rejected outright.
- Litestream to GCS with max-instances=1: no code changes, restore on
  boot, continuous WAL replication. Chosen.

## Decision

`min-instances=1 max-instances=1 --no-cpu-throttling`, Litestream
replicating to gs://catdv-annotator-db. The pin is *correctness*, not
cost. Rolling deploys (brief old+new overlap; old stops writing on
SIGTERM) are fine; traffic-split canaries are forbidden.

## Consequences

- Scaling out requires the Postgres migration first; revisit only if
  the seat limit and user count actually change.
- Deploy flags in .github/workflows/deploy.yml are load-bearing; the
  comment there points here.
- Litestream is the signal parent (entrypoint `exec`), preserving the
  lifespan seat-release on SIGTERM and the final WAL sync.
