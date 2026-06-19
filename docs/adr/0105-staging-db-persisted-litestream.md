# 0105. Staging SQLite is persisted via its own Litestream replica path

**Date:** 2026-06-19
**Status:** Accepted

## Context

Staging (`catdv-annotator-staging`) ran with an **ephemeral** SQLite DB: its
`staging.env.yaml` omitted `LITESTREAM_REPLICA_URL`, so `entrypoint.sh` skipped
the `litestream restore` + `litestream replicate` path and ran uvicorn against a
throwaway `/data/app.db`. Combined with scale-to-zero (`min-instances=0`), the DB
reset on every cold start — admin grants, roles, batches, version history, all
gone. That was a deliberate "disposable soak env" choice (ADRs 0066/0077), but in
practice staging is now used as a real environment (VPN/CatDV enabled per ADR
0104), and a DB that vanishes between cold starts makes it unusable: you can't
even stay seeded as a non-`ADMIN_EMAILS` admin, request-access state is lost, etc.

The original reason for *not* persisting was the corruption guard: Litestream
must not have two writers on one replica path, and prod already writes to
`gcs://catdv-annotator-db/litestream`. So persistence was avoided rather than given
its own path.

## Alternatives

- **Keep staging ephemeral (status quo).** Rejected — the user needs staging
  state to survive cold starts; persistence should be the default for a real env.
- **Reuse prod's replica path.** Hard no — two Litestream writers on
  `gcs://catdv-annotator-db/litestream` corrupts the replica.
- **Separate bucket (`catdv-annotator-db-staging`).** Cleanest isolation, but
  needs a new bucket + IAM grant for the runtime SA. More infra than required.
- **Same bucket, distinct prefix (chosen).** `gcs://catdv-annotator-db/staging` —
  a non-overlapping prefix in the existing bucket. The runtime SA
  (`catdv-annotator@catdav…`, shared with prod) already has access, so no new IAM.

## Decision

Persist staging's DB with its own Litestream replica:

- `staging.env.yaml`: `LITESTREAM_REPLICA_URL: "gcs://catdv-annotator-db/staging"`
  — distinct from prod's `.../litestream`. `entrypoint.sh` restores from it on
  boot and runs `litestream replicate`.
- CI `deploy-staging` **and** `deploy/deploy-staging.sh`: add
  `--no-cpu-throttling`. Under scale-to-zero, Litestream needs CPU *between*
  requests and during the SIGTERM teardown to flush the final replication;
  without it the last writes are lost on every cold start (this is exactly why
  prod carries the flag — ADR 0077). This is mandatory, not optional, once
  Litestream is enabled.

## Consequences

- Staging DB survives cold starts: admin seeding, granted roles, batches, version
  history persist. Staging now behaves like a real persistent environment.
- **The two replica paths MUST stay distinct.** `gcs://catdv-annotator-db/staging`
  vs prod's `gcs://catdv-annotator-db/litestream` — never point either at the
  other. A future third environment needs its own prefix too.
- Storage isolation otherwise unchanged: still a separate logical DB from prod
  (different replica prefix, different `INSTANCE_ID`), just no longer ephemeral.
- Relaxes the "ephemeral staging DB" aspect of ADRs 0066/0077. The scale-to-zero
  cost story is unchanged (`--no-cpu-throttling` was already prod's posture; the
  instance still stops when idle).
- The `--no-cpu-throttling` requirement is now load-bearing on staging too — if a
  future edit drops it while Litestream is on, staging silently loses recent
  writes. The env-file header notes this.
