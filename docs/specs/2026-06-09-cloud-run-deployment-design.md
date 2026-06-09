# Cloud Run deployment

**Date:** 2026-06-09
**Status:** Approved (design)
**Reference PoC:** `futurememorylab/Archive-AI-PoC` (private) — its
`deploy-cloud-run.yml` workflow skeleton is reused with corrections;
its runtime Secret-Manager-fetch pattern is deliberately **not** reused.

## Problem

The app runs today as a single-user local tool: uvicorn on
`127.0.0.1:8765`, SQLite + media caches under `./data`, CatDV reachable
on the LAN/VPN at `192.168.1.41:8080`. We want it deployed to Cloud Run
(project `catdav`, region `europe-west3`) so it is usable away from the
dev machine, without losing data across instance restarts and without
breaking the offline-degradation behavior the app already has.

Four technical obstacles, each addressed by one phase below:

1. The local proxy cache that makes playback fast does not survive
   Cloud Run's ephemeral filesystem.
2. SQLite needs durability across instance restarts and deploys.
3. CatDV lives on a private office network; Cloud Run has no TUN
   device, so kernel WireGuard is unavailable.
4. A deployed URL invites multiple users; the app has no protection
   against concurrent edits of the same annotation.

## Decisions already fixed (cross-cutting)

**One instance, always.** The Cloud Run service runs with
`min-instances=1 max-instances=1 --no-cpu-throttling`. This is
load-bearing three times over: (a) every instance takes one of CatDV's
two session seats, and in practice only one seat is free; (b)
Litestream requires a single writer per replica path; (c) the
in-process write queue serializes SQLite writes only within one
process. Never raise max-instances; never use traffic-split canaries
with this service. `--no-cpu-throttling` keeps the background loops
(sync engine, prefetch queue, LRU eviction, health probe) alive between
requests.

**Access is private, auth is deferred.** The service deploys with
`--no-allow-unauthenticated`; `roles/run.invoker` is granted to the
operator's Google account only. Access is via
`gcloud run services proxy catdv-annotator --region europe-west3`
(which serves the app on localhost with identity tokens injected).
Multi-user auth (IAP or in-app login) is an explicit non-goal until
phase 5 lands and a second user is actually wanted.

**Config is env vars; the platform decides where they come from.**
`Settings` (`backend/app/settings.py`) already reads real environment
variables first and falls back to `.env`. Locally nothing changes
(`.env`, gitignored). In Cloud Run there is no `.env` in the image;
non-secret config arrives via a committed
`deploy/cloudrun.env.yaml` passed with `--env-vars-file`, and secrets
arrive as env vars via `--set-secrets` (Cloud Run's native Secret
Manager injection). The app never imports the Secret Manager SDK; the
dead `backend/app/secrets.py` (an unused port of the PoC's runtime
fetch pattern) is deleted. `.env.example` remains the single catalog of
all config, annotated per var with where it lives in prod
(`cloudrun.env.yaml` / `Secret Manager` / `local-only`).

| Where | What |
|---|---|
| `.env` (gitignored) | local dev, everything |
| `deploy/cloudrun.env.yaml` (committed) | prod non-secrets |
| Secret Manager via `--set-secrets` | prod secrets (CatDV password, WG private key, Gemini API key) |
| GitHub repo secrets | WIF provider + project id only — no credentials |

## Goals

- Phase 1: the app boots on Cloud Run from a CI-built image, in
  CatDV-offline mode, configured purely by env vars.
- Phase 2: the SQLite database survives restarts and deploys
  (Litestream continuous replication to GCS, restore on boot).
- Phase 3: CatDV is reachable from the cloud instance through a
  userspace WireGuard tunnel (onetun) to the existing office WG server.
- Phase 4: video playback streams from GCS via signed URLs, selected
  by a `PLAYBACK_SOURCE` env parameter (local-first in dev, GCS-first
  in cloud).
- Phase 5 (deferred implementation, designed now): optimistic
  concurrency on annotation writes — the gate for granting access to a
  second user.

Phases are independently shippable, in order; each ends green on CI and
passes its manual acceptance flow.

## Non-goals

- Multi-user authentication (IAP / OAuth / sessions).
- Cloud SQL / Postgres migration. Litestream is the persistence answer
  until multi-instance is actually needed, which the seat limit makes
  unlikely.
- Real-time collaboration (websockets, presence, CRDTs).
- Autoscaling of any kind.
- Thumbnail mirroring to GCS. Thumbnails stay CatDV-fetched with an
  ephemeral disk cache; cold instances repopulate lazily and render
  placeholders meanwhile. Revisit only if this annoys in practice.
- Office-network changes beyond adding one WireGuard peer (the WG
  server already exists and is internet-reachable).

## Phase 1 — Container, config, CI/CD

### Image

`Dockerfile` at repo root, `python:3.13-slim` base:

- `COPY --from=ghcr.io/aramperes/onetun:latest /onetun /usr/local/bin/`
  (used from phase 3; harmless before).
- `COPY --from=litestream/litestream:latest /usr/local/bin/litestream
  /usr/local/bin/` (used from phase 2; harmless before).
- Install backend deps, copy `backend/`, set `entrypoint.sh` as
  entrypoint.
- `.dockerignore` excludes `.env`, `data/`, `.secret/`, `tests/`,
  `docs/`, `.git/` — local credentials and state must never enter an
  image layer.

`entrypoint.sh` (final form, pieces activate per phase):

```sh
#!/bin/sh
set -eu

# Phase 3: userspace WireGuard, restart loop (tunnel death degrades
# the app to CatDV-offline; it must not kill the container).
if [ -n "${WG_PRIVATE_KEY:-}" ]; then
  ( while true; do
      onetun --private-key "$WG_PRIVATE_KEY" \
             --endpoint "$WG_ENDPOINT" \
             --peer-public-key "$WG_PEER_PUBKEY" \
             127.0.0.1:18080:192.168.1.41:8080 || true
      sleep 2
    done ) &
fi

# Phase 2: restore DB on a fresh instance, then replicate around the app.
if [ -n "${LITESTREAM_REPLICA_URL:-}" ]; then
  litestream restore -if-db-not-exists -if-replica-exists "$DB_PATH"
  exec litestream replicate \
    -exec "python -m uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8765}"
fi

exec python -m uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8765}
```

Signal chain on shutdown: Cloud Run SIGTERM → litestream (PID 1 via
`exec`) forwards to uvicorn → FastAPI lifespan runs `ctx.aclose()`
(releases the CatDV seat; the session DELETE gets a 3 s timeout so a
dead tunnel cannot eat the grace window) → uvicorn exits → litestream
performs its final WAL sync → exit. All inside Cloud Run's 10 s
SIGTERM grace.

### Config

- No `Settings` changes in this phase beyond honoring `$PORT` via the
  entrypoint (bind host/port come from uvicorn flags, not Settings, in
  the container).
- New committed file `deploy/cloudrun.env.yaml` with the non-secret
  prod values: `APP_ENV: prod`, `CATDV_OFFLINE: "true"` (until phase
  3), `CATDV_BASE_URL: http://127.0.0.1:18080`,
  `CATDV_CATALOG_ID: "881507"`, `GCP_PROJECT_ID: catdav`,
  `GCP_LOCATION: global` (matching the working local `.env` — Gemini
  model availability is region-bound, so don't change it as part of
  deployment), `GCS_BUCKET_NAME: catdav-proxies`,
  `ARCHIVE_PROVIDER: catdv`, `AI_INPUT_STORE: gcs`, `DATA_DIR: /data`.
- `GOOGLE_APPLICATION_CREDENTIALS` stays **unset** in Cloud Run — ADC
  falls through to the runtime service account. Only local dev sets it.
- Delete `backend/app/secrets.py` (dead code, wrong pattern).

### CI/CD

`.github/workflows/deploy.yml`, triggered on push to `main` +
`workflow_dispatch`:

1. **test** job: install deps, run `pytest` and `lint-imports`. Deploy
   is `needs: test`.
2. **deploy** job: authenticate with
   `google-github-actions/auth@v2` using **Workload Identity
   Federation** (`workload_identity_provider` + `service_account` —
   no long-lived SA key JSON, correcting the PoC's `GCP_SA_KEY`
   approach); build with Buildx for `linux/amd64`; push to Artifact
   Registry `europe-west3-docker.pkg.dev/catdav/catdv-annotator/app`
   tagged `${{ github.sha }}` and `latest`; deploy:

```
gcloud run deploy catdv-annotator \
  --image=…/app:${{ github.sha }} \
  --region=europe-west3 \
  --service-account=catdv-annotator@catdav.iam.gserviceaccount.com \
  --no-allow-unauthenticated \
  --min-instances=1 --max-instances=1 --no-cpu-throttling \
  --memory=1Gi --cpu=1 \
  --env-vars-file=deploy/cloudrun.env.yaml \
  --set-secrets="CATDV_PASSWORD=catdv-password:latest,GEMINI_API_KEY=gemini-api-key:latest"
```

3. Verify step: poll the existing `GET /api/health` (`main.py`) on the
   service URL with an identity token; fail the workflow on non-200.
   That endpoint reads only in-process connection-monitor state — it
   never touches CatDV/GCS, so it cannot consume a seat or block on
   the tunnel, and it reports `mode: offline` rather than failing when
   CatDV is unreachable.

One-time GCP setup (documented in `deploy/README.md`, not automated):
Artifact Registry repo, runtime service account
(`catdv-annotator@catdav`) with `roles/storage.objectAdmin` on the two
buckets + `roles/secretmanager.secretAccessor` on the three secrets,
WIF pool/provider bound to the GitHub repo, deployer SA with
`roles/run.admin` + `roles/iam.serviceAccountUser`, `run.invoker` grant
to the operator account.

Phase 1 ships with `CATDV_OFFLINE=true`. The app already runs fully
navigable in offline mode, so the deployment is verifiable end-to-end
before persistence or the tunnel exist (DB resets on restart until
phase 2 — acceptable for this phase only).

## Phase 2 — SQLite persistence (Litestream)

- New GCS bucket `catdav-annotator-db` (separate from media — different
  lifecycle: versioned WAL segments with retention, vs large immutable
  proxies). Configured in `/etc/litestream.yml` baked into the image:
  the `DB_PATH` database replicating to
  `gcs://catdav-annotator-db/litestream`.
- Entrypoint logic as shown in phase 1: `restore -if-db-not-exists
  -if-replica-exists` then `replicate -exec`.
- **Precondition (already satisfied):** Litestream requires
  `journal_mode=WAL`, and `db.py:16` already sets it on every
  connection. Phase 2 adds a regression test asserting the pragma so
  it cannot be silently dropped.
- Env: `LITESTREAM_REPLICA_URL` and `DB_PATH` added to
  `cloudrun.env.yaml`. Local dev leaves them unset → entrypoint's
  plain-uvicorn branch (and local dev doesn't use the container
  anyway).
- Record as ADR: max-instances is pinned to 1 *for correctness*, not
  cost; rolling deploys (brief old+new overlap where the old instance
  has stopped writing) are fine, traffic splits are not.

## Phase 3 — WireGuard tunnel (onetun)

- Generate a dedicated WG keypair for the cloud peer; add the public
  key as a new peer on the existing office WireGuard server with an
  `AllowedIPs` entry routing to `192.168.1.41/32` only (least
  privilege: the cloud instance needs CatDV, not the whole LAN).
- Private key → Secret Manager secret `wg-private-key`, injected as
  `WG_PRIVATE_KEY` via `--set-secrets`. `WG_ENDPOINT` (the office
  server's public `host:port`) and `WG_PEER_PUBKEY` are not secrets and
  live in `cloudrun.env.yaml`.
- onetun runs backgrounded in the entrypoint under a `while true`
  restart loop, forwarding `127.0.0.1:18080 → 192.168.1.41:8080`. It is
  a userspace WireGuard implementation (no TUN, no NET_ADMIN), so it
  runs unprivileged inside the existing container — no sidecar.
- Flip `CATDV_OFFLINE` to `"false"` in `cloudrun.env.yaml`. The app's
  CatDV base URL is already `http://127.0.0.1:18080` from phase 1.
- **No new error handling.** Tunnel death = CatDV unreachable, which
  the app already degrades through: `LiveCtx` routes return typed 503s,
  the offline banner shows, the health probe reconnects when the
  tunnel returns. That existing behavior is the acceptance test.
- Seat-limit caveat, recorded here for operations: Cloud Run *can*
  kill an instance without grace (rare). That leaks the CatDV
  JSESSIONID until server-side idle timeout — the same failure mode as
  a local `kill -9` today. Mitigation is unchanged: wait, or kick the
  session in the CatDV admin UI.

## Phase 4 — Video playback from GCS (signed URLs)

### Current behavior

`routes/media.py::stream_media` calls
`ctx.proxy_resolver.path_for_clip_id(clip_id) -> Path` and serves the
local file (`FileResponse` / range-aware `StreamingResponse`). On a
cloud instance the local cache is ephemeral and cold, so every play
would require a multi-GB fetch through the tunnel first. But the
proxies already live in GCS (`gs://catdav-proxies`, indexed by the
`ai_store_files` table) for Gemini input.

### Design

The `ProxyResolver` protocol is **not** changed — it is a
`Path`-returning contract used widely. Instead a small `MediaLocator`
service (new, `services/media_locator.py`) sits in front of it for the
playback route only:

```
locate(clip_id) -> LocalFile(path) | RemoteUrl(url) | raises MediaNotAvailable
```

It consults the two existing cache layers in an order chosen by a new
setting:

```python
playback_source: Literal["local", "gcs"] = "local"
```

- `local` (default; dev): proxy resolver first (today's behavior); on
  `ProxyNotFound`, ask `ai_store.status(clip_key)` (a DB lookup, no
  network) and return a signed URL on hit. Side effect: clips already
  in GCS become playable in dev even when CatDV is offline and the
  local cache is cold.
- `gcs` (set in `cloudrun.env.yaml`): AI store first → V4 signed URL
  (1 h expiry, generated via the runtime service account); local
  resolver as fallback (covers studio uploads and anything already on
  the ephemeral disk).

The setting is a **preference order, not an exclusive mode** — both
layers are always consulted. A miss on both behaves exactly like
today's `ProxyNotFound`: 404 → placeholder + user-initiated prefetch.
In cloud mode prefetch routes through the tunnel and
`ai_store.ensure_uploaded`, after which playback hits the GCS path.

`stream_media` becomes: `LocalFile` → existing file-serving code
unchanged; `RemoteUrl` → `RedirectResponse(307)`. The `<video>` element
follows the redirect and issues range requests directly against GCS —
bytes never transit the Cloud Run instance. Signed-URL generation uses
`iam.serviceAccounts.signBlob` via ADC (no private key file on the
instance); the runtime SA needs `roles/iam.serviceAccountTokenCreator`
on itself for this.

Cache-layer discipline (CLAUDE.md) is preserved: the locator asks each
layer "do you have it" through its existing interface and never
fetches, never touches GCS or CatDV directly.

Thumbnails are explicitly unchanged (see Non-goals).

## Phase 5 — Optimistic concurrency (designed now, implemented before any second user)

- Add an integer `version` column (default 0) to annotation-bearing
  tables (annotations/scenes/markers/notes — exact table list fixed at
  implementation time from the current schema; one migration).
- Edit partials carry the row's `version` in a hidden field. Update
  statements become `UPDATE … SET …, version = version + 1 WHERE id = ?
  AND version = ?`; rowcount 0 → typed 409 with the fresh partial in
  the response body.
- Frontend: on 409, push `Alpine.store('toast').push("This annotation
  was changed by someone else — refreshed", {level: 'error'})` and swap
  the fresh partial in place (existing HTMX error-discipline; no
  `location.reload()`).
- Writes between *different* rows need nothing — the write queue
  already serializes them safely.
- **This phase is the precondition for granting `run.invoker` (or any
  future IAP/auth) to a second account.** Until it ships, access stays
  single-operator.

## Error handling summary

| Failure | Behavior | New code? |
|---|---|---|
| Tunnel down / CatDV unreachable | Existing offline degradation (503s, banner, reconnect probe) | No |
| GCS unreachable | `ai_store.status` raises → locator falls through to other layer → `MediaNotAvailable` names the layer | Locator only |
| Both layers miss | Today's `ProxyNotFound` path: placeholder + prefetch | No |
| Litestream replica unreachable at boot | `restore` fails → container exits → Cloud Run retries; alert via Cloud Run startup failures | No |
| SIGTERM during CatDV call | lifespan `aclose()` with 3 s timeout on session DELETE | Timeout only |
| Ungraceful instance kill | Leaked seat until CatDV idle timeout (same as local `kill -9`) | No — operational note |

## Testing

TDD per phase (project discipline):

- **Phase 1:** unit test that `Settings` resolves pure-env config with
  no `.env` present; existing `GET /api/health` covers the CI verify
  step (no new endpoint); CI workflow validated by its own `test` job
  gating;
  container boot smoke-tested locally with `docker run` +
  `CATDV_OFFLINE=true` before first deploy.
- **Phase 2:** WAL-mode assertion test on the DB layer; manual
  kill-and-restore drill (flow 3 below) — Litestream itself is not
  unit-tested, the integration drill is the evidence.
- **Phase 3:** no new app code → covered by existing offline-mode
  tests plus flow 4.
- **Phase 4:** unit tests for `MediaLocator` ordering (`local` vs
  `gcs` preference, each layer hit/miss combination, both-miss raises);
  integration test that `stream_media` returns 307 for a GCS-backed
  clip and the existing file response for a local one; query-count
  guard if any per-clip hydration is added (ADR 0046).
- **Phase 5:** integration test — two read-modify-write cycles with
  stale version → second gets 409 + fresh partial; repo-level test for
  the atomic version-checked UPDATE.

## Manual acceptance flows

A reviewer who didn't write the code follows these on the deployed
service (via `gcloud run services proxy catdv-annotator
--region europe-west3`, serving on `http://localhost:8080`). Flows are
ordered by phase; run the flows for every phase up to the one being
accepted.

1. **Phase 1 — boots offline, config from env.**
   - Push to `main`; the deploy workflow runs test → build → deploy
     green.
   - Open the proxied URL. The app loads, shows the CatDV-offline
     banner, and the clip list renders (placeholders allowed). No
     stack traces in Cloud Run logs mentioning missing `.env` or
     credentials.
   - Hitting the service URL directly in a browser (no proxy) returns
     403 — proving `--no-allow-unauthenticated` is in force.

2. **Phase 1 — local dev unaffected.**
   - On the dev machine, `run.sh` (no container) with the existing
     `.env` still boots against the LAN CatDV exactly as before this
     work.

3. **Phase 2 — data survives an instance replacement.**
   - Via the proxied UI, create a visible change (e.g. add a note to a
     clip).
   - Force a new instance: deploy any no-op change (or
     `gcloud run services update catdv-annotator --region europe-west3
     --update-labels bump=$(date +%s)`).
   - Reload the UI after the new instance is serving: the note is
     still there. Cloud Run logs for the old instance show the
     shutdown sequence (application shutdown complete → litestream
     final sync) within the grace window.

4. **Phase 3 — CatDV online through the tunnel, degrades cleanly.**
   - With the office WG peer up, the proxied UI shows CatDV connected;
     opening a clip fetches fresh metadata/thumbnails.
   - Disable the cloud peer on the office WG server (or stop the WG
     server briefly). Within the health-probe interval the UI flips to
     the offline banner; the app stays fully navigable; cached clips
     still open.
   - Re-enable the peer: the UI recovers to online without a restart.
   - Check the CatDV admin session list: exactly one session from the
     cloud instance (the seat discipline holds).

5. **Phase 4 — playback from GCS.**
   - Pick a clip known to be in the AI store (`ai_store_files` row
     exists). Press play in the proxied UI: video starts within a
     couple of seconds; browser devtools network tab shows the media
     request redirecting (307) to a `storage.googleapis.com` signed
     URL, and seeking issues range requests against that URL — not
     against the app.
   - Pick a clip *not* in GCS and not locally cached: the player shows
     the existing unavailable/prefetch affordance, not an error page.
     Trigger prefetch; once complete, play works per the bullet above.
   - On the dev machine with `PLAYBACK_SOURCE=local` (default),
     playback of a locally-cached clip still serves from disk (no
     redirect in devtools).

6. **Phase 5 — concurrent edit conflict.**
   - Open the same annotation in two browser windows (same operator
     account is fine).
   - Edit and save in window A. Then edit and save in window B without
     reloading: window B gets an error toast saying the annotation
     changed and its panel refreshes to show A's version. No silent
     overwrite; no full-page reload.

## Phasing / PR plan

| Phase | PR | Ships when |
|---|---|---|
| 1 | Dockerfile + entrypoint + `deploy/` + workflow + delete `secrets.py` | Flows 1–2 pass |
| 2 | litestream.yml + WAL assertion + bucket + env | Flow 3 passes |
| 3 | WG peer + secrets + env flip | Flow 4 passes |
| 4 | `MediaLocator` + `playback_source` + route change | Flow 5 passes |
| 5 | version column + 409 + toast (deferred until a second user is wanted) | Flow 6 passes |
