# Cloud Run Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy catdv-annotator to Cloud Run (project `catdav`, region `europe-west3`) as a pinned single instance with durable SQLite (Litestream), CatDV over a userspace WireGuard tunnel (onetun), and video playback via GCS signed URLs.

**Architecture:** One container, three processes: onetun (backgrounded, env-gated), litestream (PID-1-adjacent via `exec`, env-gated), uvicorn (litestream's child). Config is env vars everywhere — `.env` locally, `deploy/cloudrun.env.yaml` + Secret Manager in prod. A new `MediaLocator` service orders the two existing cache layers (local proxy cache, GCS AI store) by a `PLAYBACK_SOURCE` setting.

**Tech Stack:** FastAPI/uvicorn, aiosqlite, Litestream 0.3.x, onetun 0.3.x, google-cloud-storage (V4 signed URLs), GitHub Actions with Workload Identity Federation, Artifact Registry.

**Spec:** `docs/specs/2026-06-09-cloud-run-deployment-design.md`. This plan covers **phases 1–4**. Phase 5 (optimistic concurrency) is deliberately deferred per the spec — it gets its own plan when a second user is wanted. Do not implement it.

**Conventions for every task:** run tests with `.venv/bin/python -m pytest` (never system python). Commit after every green task. Tasks 6, 8 (acceptance part), 10, and 15 (acceptance part) need a human operator with `gcloud` auth and access to the office WireGuard server — the agent prepares files and commands; the operator runs the cloud-side steps.

---

## Phase 1 — Container, config, CI/CD

### Task 1: Delete dead `secrets.py` and its dependency

`backend/app/secrets.py` is an unused port of the PoC's runtime-Secret-Manager-fetch pattern. The spec rejects that pattern (Cloud Run injects secrets as env vars; the app never imports the Secret Manager SDK).

**Files:**
- Delete: `backend/app/secrets.py`
- Modify: `pyproject.toml` (remove `google-cloud-secret-manager` from `dependencies`)

- [x] **Step 1: Verify nothing imports it**

Run: `grep -rn "app.secrets\|google.cloud import secretmanager\|secret_manager" backend/ tests/ --include="*.py" | grep -v "backend/app/secrets.py"`
Expected: no output (already verified during planning; re-verify in case of drift).

- [x] **Step 2: Delete the module and the dependency**

```bash
git rm backend/app/secrets.py
```

In `pyproject.toml`, delete the line:

```toml
  "google-cloud-secret-manager>=2.20",
```

- [x] **Step 3: Reinstall and run the full suite**

Run: `.venv/bin/pip install -q -e ".[dev]" && .venv/bin/python -m pytest`
Expected: all tests pass (the module had zero callers).

- [x] **Step 4: Commit**

```bash
git add -A
git commit -m "Remove dead secrets.py and Secret Manager SDK dependency

Cloud Run injects secrets as env vars (--set-secrets); the app reads
plain env. Runtime SDK fetch was an unused PoC pattern."
```

### Task 2: Settings resolve from pure env (no `.env`)

Proves the Cloud Run config path: a container has no `.env`, everything arrives as real environment variables.

**Files:**
- Test: `tests/unit/test_settings_pure_env.py` (create)

- [x] **Step 1: Write the failing-or-passing test (regression guard)**

```python
"""Settings must resolve from OS env alone — the Cloud Run container has
no .env file; deploy/cloudrun.env.yaml + --set-secrets provide real env
vars. Guards against anyone making .env mandatory."""

import os

from backend.app.settings import Settings

_REQUIRED = {
    "CATDV_BASE_URL": "http://127.0.0.1:18080",
    "CATDV_CATALOG_ID": "881507",
    "GCP_PROJECT_ID": "catdav",
    "GCS_BUCKET_NAME": "catdav-proxies",
}


def test_settings_resolve_from_pure_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # no .env in cwd
    for key in list(os.environ):
        if key.startswith(("CATDV_", "GCP_", "GCS_", "APP_", "DATA_")):
            monkeypatch.delenv(key, raising=False)
    for key, value in _REQUIRED.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("DATA_DIR", "/data")

    s = Settings()

    assert s.app_env == "prod"
    assert s.catdv_base_url == "http://127.0.0.1:18080"
    assert s.catdv_catalog_id == 881507
    assert str(s.data_dir) == "/data"
    assert s.google_application_credentials is None  # ADC in cloud
```

- [x] **Step 2: Run it**

Run: `.venv/bin/python -m pytest tests/unit/test_settings_pure_env.py -v`
Expected: PASS (pydantic-settings already prefers OS env and tolerates a missing env_file). If it fails, the failure is the bug to fix — do not weaken the test.

- [x] **Step 3: Commit**

```bash
git add tests/unit/test_settings_pure_env.py
git commit -m "Test: Settings resolve from pure env without .env (Cloud Run contract)"
```

### Task 3: entrypoint, litestream config, Dockerfile, .dockerignore

All three runtime processes wired now, env-gated so phase 1 deploys run plain uvicorn. `DB_PATH` must be `/data/app.db` — `context.py:129` derives the DB as `settings.data_dir / "app.db"` and `cloudrun.env.yaml` sets `DATA_DIR: /data`.

**Files:**
- Create: `deploy/entrypoint.sh`
- Create: `deploy/litestream.yml`
- Create: `Dockerfile` (repo root)
- Create: `.dockerignore` (repo root)

- [x] **Step 1: Write `deploy/entrypoint.sh`**

```sh
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
```

- [x] **Step 2: Write `deploy/litestream.yml`**

```yaml
# Litestream config baked into the image. Both values come from env
# (cloudrun.env.yaml): DB_PATH=/data/app.db (must equal
# settings.data_dir / "app.db"), LITESTREAM_REPLICA_URL=
# gcs://catdav-annotator-db/litestream. GCS auth is ADC via the
# runtime service account.
dbs:
  - path: ${DB_PATH}
    replicas:
      - url: ${LITESTREAM_REPLICA_URL}
```

- [x] **Step 3: Write `Dockerfile`**

```dockerfile
FROM python:3.13-slim

# Static binaries for phases 2-3; inert until their env vars are set.
# If a tag 404s at build time, check the latest release on
# github.com/aramperes/onetun / github.com/benbjohnson/litestream and
# pin that instead -- keep it pinned, never :latest.
COPY --from=ghcr.io/aramperes/onetun:0.3.10 /onetun /usr/local/bin/onetun
COPY --from=litestream/litestream:0.3.13 /usr/local/bin/litestream /usr/local/bin/litestream

WORKDIR /srv/app
COPY pyproject.toml ./
COPY backend ./backend
# Editable install keeps templates/static/seeds readable from the
# source tree (pyproject has no package-data config; run.sh and the
# systemd deploy install editable too).
RUN pip install --no-cache-dir -e .

COPY deploy/litestream.yml /etc/litestream.yml
COPY deploy/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh && mkdir -p /data

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
```

- [x] **Step 4: Write `.dockerignore`**

```
.env
.secret/
.venv/
.git/
.github/
.claude/
data/
logs/
docs/
tests/
tools/
scripts/
*.egg-info/
__pycache__/
**/__pycache__/
```

`.env`, `data/`, `.secret/` are the load-bearing lines — local credentials and state must never enter an image layer.

- [x] **Step 5: Syntax-check the entrypoint**

Run: `sh -n deploy/entrypoint.sh && echo OK`
Expected: `OK`

- [ ] **Step 6: Build and smoke-test the container locally (skip build if Docker unavailable)**

```bash
docker build -t catdv-annotator:dev .
docker run --rm -p 18765:8765 \
  -e APP_ENV=prod -e CATDV_OFFLINE=true \
  -e CATDV_BASE_URL=http://127.0.0.1:18080 -e CATDV_CATALOG_ID=881507 \
  -e GCP_PROJECT_ID=catdav -e GCS_BUCKET_NAME=catdav-proxies \
  -e DATA_DIR=/data \
  catdv-annotator:dev &
sleep 5
curl -fsS http://127.0.0.1:18765/api/health
```

Expected: `{"status":"ok","mode":...}`. Stop the container with `docker stop` (SIGTERM — never `kill -9`, same seat discipline as dev). If Docker is not installed on this machine, note it and rely on the first Cloud Run deploy (Task 6) as the smoke test.

- [x] **Step 7: Commit**

```bash
git add Dockerfile .dockerignore deploy/entrypoint.sh deploy/litestream.yml
git commit -m "Container: Dockerfile + entrypoint (uvicorn/litestream/onetun, env-gated)"
```

### Task 4: `deploy/cloudrun.env.yaml` + one-time GCP setup doc

**Files:**
- Create: `deploy/cloudrun.env.yaml`
- Create: `deploy/README.md`
- Modify: `docs/DEPLOY.md` (add a pointer)

- [x] **Step 1: Write `deploy/cloudrun.env.yaml`**

Copy `CATDV_USERNAME`'s value from the local `.env` (it is not a secret; the password is).

```yaml
# Non-secret prod config for Cloud Run (gcloud run deploy --env-vars-file).
# Secrets (CATDV_PASSWORD, GEMINI_API_KEY, later WG_PRIVATE_KEY) are
# injected via --set-secrets in .github/workflows/deploy.yml -- never here.
# All values must be YAML strings.
APP_ENV: "prod"
DATA_DIR: "/data"
# Flipped to "false" in phase 3 when the WireGuard tunnel lands.
CATDV_OFFLINE: "true"
# CatDV is reached through the onetun local forward (phase 3).
CATDV_BASE_URL: "http://127.0.0.1:18080"
CATDV_USERNAME: "<copy from local .env>"
CATDV_CATALOG_ID: "881507"
GCP_PROJECT_ID: "catdav"
# Matches the working local .env -- Gemini model availability is
# region-bound; do not change as part of deployment.
GCP_LOCATION: "global"
GCS_BUCKET_NAME: "catdav-proxies"
ARCHIVE_PROVIDER: "catdv"
AI_INPUT_STORE: "gcs"
```

(Replace `<copy from local .env>` with the actual username before committing — it is non-secret.)

- [x] **Step 2: Write `deploy/README.md` — the one-time GCP setup**

```markdown
# Cloud Run deployment — one-time GCP setup

Everything here is run once by an operator with `gcloud` auth on
project `catdav`. Day-to-day deploys are the GitHub Actions workflow
(`.github/workflows/deploy.yml`). Spec:
`docs/specs/2026-06-09-cloud-run-deployment-design.md`.

```bash
export PROJECT_ID=catdav
export REGION=europe-west3
export REPO=catdv-annotator
export RUNTIME_SA=catdv-annotator@${PROJECT_ID}.iam.gserviceaccount.com
export DEPLOYER_SA=github-deployer@${PROJECT_ID}.iam.gserviceaccount.com

# 1. APIs
gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com iamcredentials.googleapis.com \
  storage.googleapis.com --project=$PROJECT_ID

# 2. Artifact Registry
gcloud artifacts repositories create $REPO --repository-format=docker \
  --location=$REGION --project=$PROJECT_ID

# 3. Runtime service account (reuse if scripts/setup-gcp.sh already made it)
gcloud iam service-accounts create catdv-annotator --project=$PROJECT_ID || true
# Media bucket (exists) + Litestream bucket (created in phase 2)
gsutil iam ch serviceAccount:${RUNTIME_SA}:roles/storage.objectAdmin gs://catdav-proxies
# Signed URLs via IAM signBlob (phase 4): the SA signs as itself
gcloud iam service-accounts add-iam-policy-binding $RUNTIME_SA \
  --member=serviceAccount:$RUNTIME_SA \
  --role=roles/iam.serviceAccountTokenCreator --project=$PROJECT_ID

# 4. Secrets (values prompted; never on the command line via args)
printf '%s' "$CATDV_PASSWORD_VALUE" | gcloud secrets create catdv-password \
  --data-file=- --replication-policy=automatic --project=$PROJECT_ID
printf '%s' "$GEMINI_API_KEY_VALUE" | gcloud secrets create gemini-api-key \
  --data-file=- --replication-policy=automatic --project=$PROJECT_ID
for s in catdv-password gemini-api-key; do
  gcloud secrets add-iam-policy-binding $s \
    --member=serviceAccount:$RUNTIME_SA \
    --role=roles/secretmanager.secretAccessor --project=$PROJECT_ID
done

# 5. Workload Identity Federation for GitHub Actions (no SA keys)
gcloud iam workload-identity-pools create github --location=global \
  --project=$PROJECT_ID
gcloud iam workload-identity-pools providers create-oidc github-oidc \
  --location=global --workload-identity-pool=github \
  --issuer-uri=https://token.actions.githubusercontent.com \
  --attribute-mapping=google.subject=assertion.sub,attribute.repository=assertion.repository \
  --attribute-condition="assertion.repository=='<github-org>/<repo>'" \
  --project=$PROJECT_ID
gcloud iam service-accounts create github-deployer --project=$PROJECT_ID
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
gcloud iam service-accounts add-iam-policy-binding $DEPLOYER_SA \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/attribute.repository/<github-org>/<repo>" \
  --role=roles/iam.workloadIdentityUser --project=$PROJECT_ID
for role in roles/run.admin roles/artifactregistry.writer roles/iam.serviceAccountUser; do
  gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member=serviceAccount:$DEPLOYER_SA --role=$role
done
# The workflow's verify step calls the private URL with an id token:
gcloud run services add-iam-policy-binding catdv-annotator --region=$REGION \
  --member=serviceAccount:$DEPLOYER_SA --role=roles/run.invoker \
  --project=$PROJECT_ID   # run AFTER the first deploy creates the service

# 6. Operator access to the private service
gcloud run services add-iam-policy-binding catdv-annotator --region=$REGION \
  --member=user:peter.hora@gmail.com --role=roles/run.invoker \
  --project=$PROJECT_ID   # run AFTER the first deploy

# 7. GitHub repo secrets (Settings -> Secrets -> Actions)
#    GCP_WIF_PROVIDER = projects/<PROJECT_NUMBER>/locations/global/workloadIdentityPools/github/providers/github-oidc
#    GCP_DEPLOYER_SA  = github-deployer@catdav.iam.gserviceaccount.com
```

## Accessing the deployed app

```bash
gcloud run services proxy catdv-annotator --region europe-west3
# then open http://localhost:8080
```

## Phase 2 (run when phase 2 lands)

```bash
gsutil mb -p $PROJECT_ID -l $REGION gs://catdav-annotator-db
gsutil iam ch serviceAccount:${RUNTIME_SA}:roles/storage.objectAdmin gs://catdav-annotator-db
```

## Phase 3 (run when phase 3 lands)

On a trusted machine: `wg genkey | tee /tmp/wg-cloud.key | wg pubkey`.
Add the public key as a peer on the office WireGuard server with
`AllowedIPs = <WG_SOURCE_IP>/32` for the new peer, and ensure the
cloud peer's allowed route covers only `192.168.1.41/32` (least
privilege — CatDV, not the whole LAN). Then:

```bash
gcloud secrets create wg-private-key --data-file=/tmp/wg-cloud.key \
  --replication-policy=automatic --project=$PROJECT_ID
gcloud secrets add-iam-policy-binding wg-private-key \
  --member=serviceAccount:$RUNTIME_SA \
  --role=roles/secretmanager.secretAccessor --project=$PROJECT_ID
shred -u /tmp/wg-cloud.key
```

Fill `WG_ENDPOINT` / `WG_PEER_PUBKEY` / `WG_SOURCE_IP` in
`deploy/cloudrun.env.yaml` from the office server's public host:port,
its public key, and the tunnel IP assigned to the new peer.
```

Replace `<github-org>/<repo>` with the actual GitHub `owner/name` of this repository (check `git remote -v`).

- [x] **Step 3: Add a pointer in `docs/DEPLOY.md`**

At the top of `docs/DEPLOY.md`, after the intro line, add:

```markdown
> **Cloud Run:** see `deploy/README.md` (one-time GCP setup) and
> `docs/specs/2026-06-09-cloud-run-deployment-design.md`. The sections
> below describe the original Mac-dev / CatDV-server systemd deploy.
```

- [x] **Step 4: Commit**

```bash
git add deploy/cloudrun.env.yaml deploy/README.md docs/DEPLOY.md
git commit -m "Deploy: Cloud Run env file + one-time GCP setup runbook"
```

### Task 5: GitHub Actions workflow

**Files:**
- Create: `.github/workflows/deploy.yml`

- [x] **Step 1: Write the workflow**

```yaml
name: Deploy to Cloud Run

on:
  push:
    branches: [main]
  workflow_dispatch:

env:
  PROJECT_ID: catdav
  REGION: europe-west3
  SERVICE: catdv-annotator
  IMAGE: europe-west3-docker.pkg.dev/catdav/catdv-annotator/app

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      - name: Install
        run: pip install -e ".[dev]"
      - name: Tests
        run: python -m pytest
      - name: Import contracts
        run: lint-imports

  deploy:
    needs: test
    runs-on: ubuntu-latest
    permissions:
      contents: read
      id-token: write
    steps:
      - uses: actions/checkout@v4
      - name: Authenticate to Google Cloud (WIF, keyless)
        uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: ${{ secrets.GCP_WIF_PROVIDER }}
          service_account: ${{ secrets.GCP_DEPLOYER_SA }}
      - uses: google-github-actions/setup-gcloud@v2
      - name: Configure Docker for Artifact Registry
        run: gcloud auth configure-docker europe-west3-docker.pkg.dev --quiet
      - name: Build and push
        run: |
          docker build --platform linux/amd64 \
            -t "$IMAGE:${{ github.sha }}" -t "$IMAGE:latest" .
          docker push "$IMAGE:${{ github.sha }}"
          docker push "$IMAGE:latest"
      - name: Deploy
        # min/max-instances=1 is correctness, not cost: one CatDV seat,
        # one Litestream writer, one in-process write queue. Never raise
        # it; never traffic-split. See spec + ADR.
        run: |
          gcloud run deploy "$SERVICE" \
            --image="$IMAGE:${{ github.sha }}" \
            --region="$REGION" \
            --service-account=catdv-annotator@${PROJECT_ID}.iam.gserviceaccount.com \
            --no-allow-unauthenticated \
            --min-instances=1 --max-instances=1 --no-cpu-throttling \
            --memory=1Gi --cpu=1 \
            --env-vars-file=deploy/cloudrun.env.yaml \
            --set-secrets="CATDV_PASSWORD=catdv-password:latest,GEMINI_API_KEY=gemini-api-key:latest"
      - name: Verify /api/health
        run: |
          URL=$(gcloud run services describe "$SERVICE" --region="$REGION" \
            --format='value(status.url)')
          TOKEN=$(gcloud auth print-identity-token)
          curl -fsS -H "Authorization: Bearer $TOKEN" "$URL/api/health"
```

- [x] **Step 2: Validate YAML locally**

Run: `.venv/bin/python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/deploy.yml')); print('OK')"`
(If `yaml` is missing, `pip install pyyaml` into the venv first.)
Expected: `OK`

- [x] **Step 3: Commit**

```bash
git add .github/workflows/deploy.yml
git commit -m "CI/CD: build + deploy to Cloud Run (WIF auth, test-gated, single instance)"
```

### Task 6: First deploy + phase-1 acceptance (operator)

No code. Operator runs `deploy/README.md` steps 1–7, sets the two GitHub secrets, pushes `main` (or runs `workflow_dispatch`), then walks **manual acceptance flows 1 and 2** from the spec:

- [ ] Flow 1: workflow green; proxied UI loads with the CatDV-offline banner; direct unauthenticated URL hit returns 403; no missing-config stack traces in Cloud Run logs.
- [ ] Flow 2: local `./run.sh` (no container) still boots against LAN CatDV with the existing `.env`.
- [ ] Run the two post-first-deploy IAM bindings from `deploy/README.md` (deployer + operator `run.invoker`).

---

## Phase 2 — SQLite persistence (Litestream)

### Task 7: WAL-mode regression test

`db.py:16` already sets `PRAGMA journal_mode=WAL`. Litestream depends on it; pin it.

**Files:**
- Test: `tests/unit/test_db_wal_pragma.py` (create)

- [x] **Step 1: Write the test**

```python
"""Litestream (deploy/litestream.yml) requires WAL journaling; open_db
must keep setting it. If this fails, cloud persistence silently breaks."""

from backend.app.db import open_db


async def test_open_db_sets_wal(tmp_path):
    async with open_db(tmp_path / "t.db") as conn:
        cur = await conn.execute("PRAGMA journal_mode")
        row = await cur.fetchone()
    assert row[0].lower() == "wal"
```

- [x] **Step 2: Run it**

Run: `.venv/bin/python -m pytest tests/unit/test_db_wal_pragma.py -v`
Expected: PASS

- [x] **Step 3: Commit**

```bash
git add tests/unit/test_db_wal_pragma.py
git commit -m "Test: pin WAL journal mode (Litestream precondition)"
```

### Task 8: Wire Litestream env + ADR + phase-2 acceptance

The entrypoint and `litestream.yml` already exist (Task 3); this turns them on.

**Files:**
- Modify: `deploy/cloudrun.env.yaml`
- Create: `docs/adr/0066-cloud-run-single-instance-litestream.md` (use the next free number if 0066 is taken by then)
- Modify: `docs/decisions.md` (index row)

- [ ] **Step 1: Add to `deploy/cloudrun.env.yaml`**

```yaml
# Phase 2: SQLite persistence. DB_PATH must equal
# settings.data_dir / "app.db" (context.py:129).
DB_PATH: "/data/app.db"
LITESTREAM_REPLICA_URL: "gcs://catdav-annotator-db/litestream"
```

- [ ] **Step 2: Write the ADR**

`docs/adr/0066-cloud-run-single-instance-litestream.md`, MADR-lite format (match any existing ADR):

```markdown
# 0066. Cloud Run pinned to one instance; SQLite persisted via Litestream

**Date:** <today's date>
**Status:** Accepted

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
replicating to gs://catdav-annotator-db. The pin is *correctness*, not
cost. Rolling deploys (brief old+new overlap; old stops writing on
SIGTERM) are fine; traffic-split canaries are forbidden.

## Consequences

- Scaling out requires the Postgres migration first; revisit only if
  the seat limit and user count actually change.
- Deploy flags in .github/workflows/deploy.yml are load-bearing; the
  comment there points here.
- Litestream is the signal parent (entrypoint `exec`), preserving the
  lifespan seat-release on SIGTERM and the final WAL sync.
```

Add the row to the index table in `docs/decisions.md`.

- [ ] **Step 3: Commit**

```bash
git add deploy/cloudrun.env.yaml docs/adr/0066-cloud-run-single-instance-litestream.md docs/decisions.md
git commit -m "Phase 2: enable Litestream replication; ADR 0066 single-instance pin"
```

- [ ] **Step 4 (operator): create the bucket, deploy, run acceptance flow 3**

Run the "Phase 2" block in `deploy/README.md`, push, then spec flow 3: create a note via the proxied UI → force a new instance (`gcloud run services update catdv-annotator --region europe-west3 --update-labels bump=$(date +%s)`) → note survives; old instance's logs show the full shutdown sequence.

---

## Phase 3 — WireGuard tunnel (onetun)

### Task 9: Bound the CatDV logout to 3 seconds

`CatdvClient.logout()` (`backend/app/services/catdv_client.py:94`) inherits the client-wide 60 s timeout. On shutdown that could eat Cloud Run's whole 10 s grace if the tunnel is dead, starving Litestream's final sync.

**Files:**
- Modify: `backend/app/services/catdv_client.py:103`
- Test: `tests/unit/test_catdv_logout_timeout.py` (create)

- [ ] **Step 1: Write the failing test**

```python
"""Shutdown budget: Cloud Run grants 10s after SIGTERM, shared by the
lifespan (CatDV logout) and Litestream's final WAL sync. logout() must
bound its request so a dead tunnel can't starve the sync."""

import httpx

from backend.app.services.catdv_client import CatdvClient


async def test_logout_uses_short_timeout(monkeypatch):
    client = CatdvClient("http://example.invalid", "u", "p")
    captured: dict = {}

    async with client:
        client._logged_in = True

        async def fake_delete(url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return httpx.Response(200, request=httpx.Request("DELETE", url))

        monkeypatch.setattr(client.http, "delete", fake_delete)
        await client.logout()

    assert captured["url"].endswith("/catdv/api/9/session")
    assert captured.get("timeout") == 3.0
```

- [ ] **Step 2: Run it — must fail**

Run: `.venv/bin/python -m pytest tests/unit/test_catdv_logout_timeout.py -v`
Expected: FAIL — `captured.get("timeout")` is `None`.

- [ ] **Step 3: Implement**

In `logout()`, change line 103 from:

```python
            await self.http.delete(f"{self._base}/catdv/api/9/session")
```

to:

```python
            # Bounded: on shutdown this shares Cloud Run's 10s SIGTERM
            # grace with Litestream's final WAL sync; a dead tunnel must
            # not starve it. 3s matches uvicorn --timeout-graceful-shutdown.
            await self.http.delete(f"{self._base}/catdv/api/9/session", timeout=3.0)
```

- [ ] **Step 4: Run the test and the full suite**

Run: `.venv/bin/python -m pytest tests/unit/test_catdv_logout_timeout.py tests/unit/test_aclose_ordering.py -v && .venv/bin/python -m pytest`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/catdv_client.py tests/unit/test_catdv_logout_timeout.py
git commit -m "Bound CatDV logout to 3s (Cloud Run shutdown-grace budget)"
```

### Task 10: Turn the tunnel on (operator + config edits)

**Files:**
- Modify: `deploy/cloudrun.env.yaml`
- Modify: `.github/workflows/deploy.yml` (one line)

- [ ] **Step 1 (operator): WG peer + secret** — run the "Phase 3" block in `deploy/README.md`.

- [ ] **Step 2: Update `deploy/cloudrun.env.yaml`**

Flip `CATDV_OFFLINE` and add the tunnel config (values from the office WG server):

```yaml
CATDV_OFFLINE: "false"
# Phase 3: onetun userspace WireGuard (consumed by entrypoint.sh, not Settings).
WG_ENDPOINT: "<office-public-host:port>"
WG_PEER_PUBKEY: "<office WG server public key>"
WG_SOURCE_IP: "<tunnel IP assigned to the cloud peer, e.g. 10.6.0.9>"
```

(All three are non-secret; only the private key lives in Secret Manager.)

- [ ] **Step 3: Add the secret to the deploy step**

In `.github/workflows/deploy.yml`, change the `--set-secrets` line to:

```
            --set-secrets="CATDV_PASSWORD=catdv-password:latest,GEMINI_API_KEY=gemini-api-key:latest,WG_PRIVATE_KEY=wg-private-key:latest"
```

- [ ] **Step 4: Commit, push, acceptance flow 4 (operator)**

```bash
git add deploy/cloudrun.env.yaml .github/workflows/deploy.yml
git commit -m "Phase 3: enable WireGuard tunnel to CatDV (onetun)"
```

Then spec flow 4: UI shows CatDV online; disable the office peer → offline banner within the probe interval, app stays navigable; re-enable → recovers without restart; CatDV admin shows exactly one cloud session.

---

## Phase 4 — Video playback from GCS (signed URLs)

### Task 11: `playback_source` setting

**Files:**
- Modify: `backend/app/settings.py` (one field)
- Modify: `tests/unit/test_settings_pure_env.py` (add a test)
- Modify: `.env.example`

- [ ] **Step 1: Write the failing test** (append to `tests/unit/test_settings_pure_env.py`)

```python
def test_playback_source_defaults_local_overridable(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for key, value in _REQUIRED.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("PLAYBACK_SOURCE", raising=False)
    assert Settings().playback_source == "local"
    monkeypatch.setenv("PLAYBACK_SOURCE", "gcs")
    assert Settings().playback_source == "gcs"
```

- [ ] **Step 2: Run it — must fail**

Run: `.venv/bin/python -m pytest tests/unit/test_settings_pure_env.py -v`
Expected: FAIL — `Settings` has no `playback_source`.

- [ ] **Step 3: Implement** — in `backend/app/settings.py`, after the `clip_list_cache_ttl_minutes` line, add:

```python
    # Playback byte-source preference (NOT exclusive): MediaLocator tries
    # both cache layers, this just orders them. "local" = proxy cache
    # first (dev); "gcs" = signed URL from the AI store first (cloud,
    # where local disk is ephemeral). See the Cloud Run deployment spec.
    playback_source: Literal["local", "gcs"] = "local"
```

- [ ] **Step 4: Run tests, document, commit**

Run: `.venv/bin/python -m pytest tests/unit/test_settings_pure_env.py -v`
Expected: PASS

Append to `.env.example` (after the GCP block):

```
# Playback byte-source preference: "local" (proxy cache first; dev
# default) or "gcs" (signed URLs from the AI store first; set in
# deploy/cloudrun.env.yaml for Cloud Run). Both layers are always
# consulted -- this only sets the order.
PLAYBACK_SOURCE=local
```

```bash
git add backend/app/settings.py tests/unit/test_settings_pure_env.py .env.example
git commit -m "Settings: playback_source preference (local|gcs)"
```

### Task 12: `GcsService.signed_url`

**Files:**
- Modify: `backend/app/services/gcs.py`
- Test: `tests/unit/test_gcs_signed_url.py` (create)

- [ ] **Step 1: Write the failing test**

```python
"""signed_url must parse any gs:// handle (not assume the default
bucket) and request a V4 URL. The IAM-signBlob fallback path needs real
ADC and is covered by manual acceptance flow 5, not unit tests."""

from backend.app.services.gcs import GcsService


def test_signed_url_parses_gs_uri(monkeypatch):
    captured: dict = {}

    class FakeBlob:
        def __init__(self, name):
            captured["blob"] = name

        def generate_signed_url(self, **kwargs):
            captured.update(kwargs)
            return "https://signed.example/x"

    class FakeBucket:
        def __init__(self, name):
            captured["bucket"] = name

        def blob(self, name, **kwargs):
            return FakeBlob(name)

    class FakeClient:
        def bucket(self, name):
            return FakeBucket(name)

    monkeypatch.setattr(
        "backend.app.services.gcs.storage.Client", lambda: FakeClient()
    )
    svc = GcsService("default-bucket")
    url = svc.signed_url("gs://catdav-proxies/clips/42.mov", expires_s=3600)

    assert url == "https://signed.example/x"
    assert captured["bucket"] == "catdav-proxies"
    assert captured["blob"] == "clips/42.mov"
    assert captured["version"] == "v4"
```

- [ ] **Step 2: Run it — must fail**

Run: `.venv/bin/python -m pytest tests/unit/test_gcs_signed_url.py -v`
Expected: FAIL — no attribute `signed_url`.

- [ ] **Step 3: Implement** — in `backend/app/services/gcs.py`, add imports at the top:

```python
from datetime import timedelta

import google.auth
import google.auth.transport.requests
```

and this method on `GcsService`:

```python
    def signed_url(self, gs_uri: str, *, expires_s: int = 3600) -> str:
        """V4 signed URL for a gs:// handle (e.g. an UploadedRef.handle).

        Blocking (may call the IAM credentials API) -- callers in async
        context must wrap in asyncio.to_thread. With a key file
        (GOOGLE_APPLICATION_CREDENTIALS, local dev) the library signs
        directly; on Cloud Run ADC has no private key, so fall back to
        IAM signBlob (needs roles/iam.serviceAccountTokenCreator on the
        runtime SA -- see deploy/README.md).
        """
        bucket_name, _, blob_name = gs_uri.removeprefix("gs://").partition("/")
        blob = self._client.bucket(bucket_name).blob(blob_name)
        expiration = timedelta(seconds=expires_s)
        try:
            return blob.generate_signed_url(version="v4", expiration=expiration)
        except AttributeError:
            # ADC without a private key (Cloud Run): sign via IAM.
            credentials, _ = google.auth.default()
            credentials.refresh(google.auth.transport.requests.Request())
            return blob.generate_signed_url(
                version="v4",
                expiration=expiration,
                service_account_email=credentials.service_account_email,
                access_token=credentials.token,
            )
```

- [ ] **Step 4: Run tests and commit**

Run: `.venv/bin/python -m pytest tests/unit/test_gcs_signed_url.py -v && .venv/bin/python -m pytest`
Expected: PASS

```bash
git add backend/app/services/gcs.py tests/unit/test_gcs_signed_url.py
git commit -m "GcsService.signed_url: V4 signed URLs with IAM-signBlob fallback"
```

### Task 13: `MediaLocator` service

**Files:**
- Create: `backend/app/services/media_locator.py`
- Test: `tests/unit/test_media_locator.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
"""MediaLocator ordering matrix. playback_source is a preference order,
not an exclusive mode: both layers are always consulted; a both-miss
raises MediaNotAvailable naming what each layer said."""

from pathlib import Path

import pytest

from backend.app.services.media_locator import (
    LocalFile,
    MediaLocator,
    MediaNotAvailable,
    RemoteUrl,
)


class FakeResolver:
    def __init__(self, path=None, exc=None):
        self.path, self.exc, self.calls = path, exc, 0

    async def path_for_clip_id(self, clip_id):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.path


class FakeRef:
    handle = "gs://catdav-proxies/clips/7.mov"


class FakeStore:
    def __init__(self, ref=None, exc=None):
        self.ref, self.exc, self.keys = ref, exc, []

    async def status(self, clip_key):
        self.keys.append(clip_key)
        if self.exc:
            raise self.exc
        return self.ref


class FakeGcs:
    def signed_url(self, handle, *, expires_s):
        return f"https://signed.example/{handle}"


def make(resolver, store, prefer):
    return MediaLocator(
        proxy_resolver=resolver, ai_store=store, gcs_service=FakeGcs(), prefer=prefer
    )


async def test_local_first_hits_local():
    resolver = FakeResolver(path=Path("/cache/7.mov"))
    store = FakeStore(ref=FakeRef())
    found = await make(resolver, store, "local").locate(7)
    assert found == LocalFile(Path("/cache/7.mov"))
    assert store.keys == []  # second layer never consulted on a hit


async def test_local_first_falls_back_to_gcs():
    resolver = FakeResolver(exc=FileNotFoundError("not cached"))
    store = FakeStore(ref=FakeRef())
    found = await make(resolver, store, "local").locate(7)
    assert isinstance(found, RemoteUrl)
    assert found.url.startswith("https://signed.example/gs://")
    assert store.keys == [("catdv", "7")]


async def test_gcs_first_skips_resolver_on_hit():
    resolver = FakeResolver(path=Path("/cache/7.mov"))
    store = FakeStore(ref=FakeRef())
    found = await make(resolver, store, "gcs").locate(7)
    assert isinstance(found, RemoteUrl)
    assert resolver.calls == 0


async def test_gcs_first_falls_back_to_local():
    resolver = FakeResolver(path=Path("/cache/7.mov"))
    store = FakeStore(ref=None)  # not uploaded
    found = await make(resolver, store, "gcs").locate(7)
    assert found == LocalFile(Path("/cache/7.mov"))


async def test_both_miss_raises_naming_layers():
    resolver = FakeResolver(exc=FileNotFoundError("not cached"))
    store = FakeStore(ref=None)
    with pytest.raises(MediaNotAvailable) as e:
        await make(resolver, store, "local").locate(7)
    assert "local cache" in str(e.value)
    assert "ai store" in str(e.value)


async def test_none_resolver_is_a_miss_not_a_crash():
    store = FakeStore(ref=None)
    with pytest.raises(MediaNotAvailable):
        await make(None, store, "local").locate(7)
```

- [ ] **Step 2: Run them — must fail**

Run: `.venv/bin/python -m pytest tests/unit/test_media_locator.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `backend/app/services/media_locator.py`**

```python
"""MediaLocator -- decides where playback bytes for a clip come from.

Consults the two existing cache layers through their own interfaces
(ProxyResolver for the local proxy cache, AIInputStore for GCS) in the
order given by ``settings.playback_source``. The setting is a
preference order, not an exclusive mode: both layers are always tried
before giving up, and a both-miss raises ``MediaNotAvailable`` naming
what each layer said (CLAUDE.md: errors must name WHICH cache layer
missed). The locator never fetches and never talks to CatDV/GCS
directly -- it only asks each layer "do you have it".
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from backend.app.archive.ai_store import AIInputStore
    from backend.app.services.gcs import GcsService
    from backend.app.services.proxy_resolver import ProxyResolver

SIGNED_URL_TTL_S = 3600


@dataclass(frozen=True)
class LocalFile:
    path: Path


@dataclass(frozen=True)
class RemoteUrl:
    url: str


class MediaNotAvailable(Exception):
    """Neither cache layer can serve this clip right now (transient --
    callers must NOT infer the clip is gone; see archive/errors.py)."""

    def __init__(self, clip_id: int, detail: str) -> None:
        super().__init__(f"clip {clip_id} not available: {detail}")


class MediaLocator:
    def __init__(
        self,
        *,
        proxy_resolver: "ProxyResolver | None",
        ai_store: "AIInputStore",
        gcs_service: "GcsService",
        prefer: Literal["local", "gcs"],
    ) -> None:
        self._resolver = proxy_resolver
        self._ai_store = ai_store
        self._gcs = gcs_service
        self._prefer = prefer

    async def locate(self, clip_id: int) -> LocalFile | RemoteUrl:
        attempts = (
            (self._from_local, self._from_gcs)
            if self._prefer == "local"
            else (self._from_gcs, self._from_local)
        )
        misses: list[str] = []
        for attempt in attempts:
            found = await attempt(clip_id, misses)
            if found is not None:
                return found
        raise MediaNotAvailable(clip_id, "; ".join(misses))

    async def _from_local(self, clip_id: int, misses: list[str]) -> LocalFile | None:
        if self._resolver is None:
            misses.append("local cache: resolver offline")
            return None
        try:
            return LocalFile(await self._resolver.path_for_clip_id(clip_id))
        except Exception as exc:  # fall through to the other layer, not terminal
            misses.append(f"local cache: {exc}")
            return None

    async def _from_gcs(self, clip_id: int, misses: list[str]) -> RemoteUrl | None:
        try:
            ref = await self._ai_store.status(("catdv", str(clip_id)))
        except Exception as exc:  # fall through to the other layer, not terminal
            misses.append(f"ai store: {exc}")
            return None
        if ref is None or not ref.handle.startswith("gs://"):
            misses.append("ai store: not uploaded")
            return None
        url = await asyncio.to_thread(
            self._gcs.signed_url, ref.handle, expires_s=SIGNED_URL_TTL_S
        )
        return RemoteUrl(url)
```

Notes for the implementer: the broad `except Exception` here is the deliberate "try the other layer" semantic, recorded in the miss list and surfaced in `MediaNotAvailable` — no caller infers absence from it (ADR 0042 discipline). The clip-key shape `("catdv", str(clip_id))` matches `services/annotator.py` (`clip_key=("catdv", str(clip_id))`). `signed_url` is wrapped in `asyncio.to_thread` because its IAM fallback does network I/O.

- [ ] **Step 4: Run tests and commit**

Run: `.venv/bin/python -m pytest tests/unit/test_media_locator.py -v && .venv/bin/python -m pytest`
Expected: PASS

```bash
git add backend/app/services/media_locator.py tests/unit/test_media_locator.py
git commit -m "MediaLocator: ordered two-layer playback source (local cache / GCS signed URL)"
```

### Task 14: Wire locator into `LiveCtx` and the media route

**Files:**
- Modify: `backend/app/context.py` (add a property on `LiveCtx`)
- Modify: `backend/app/routes/media.py:64-83` (the non-uploaded branch of `stream_media`)
- Test: `tests/unit/test_stream_media_locator.py` (create)

- [ ] **Step 1: Write the failing route tests**

```python
"""stream_media must serve LocalFile via the existing file path and
RemoteUrl via 307 redirect (browser range requests then hit GCS
directly), and turn MediaNotAvailable into the usual 404."""

from pathlib import Path

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.app.routes.media import router
from backend.app.services.media_locator import (
    LocalFile,
    MediaNotAvailable,
    RemoteUrl,
)


class StubLocator:
    def __init__(self, result):
        self._result = result

    async def locate(self, clip_id):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class StubLive:
    def __init__(self, locator):
        self.media_locator = locator


def make_app(locator):
    app = FastAPI()
    app.include_router(router)
    app.state.live_ctx = StubLive(locator)
    app.state.core_ctx = None  # not consulted for non-uploaded clip ids
    return app


async def _get(app, path, **kwargs):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.get(path, **kwargs)


async def test_remote_url_redirects_307():
    app = make_app(StubLocator(RemoteUrl("https://storage.googleapis.com/b/c?sig=1")))
    resp = await _get(app, "/api/media/123", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"].startswith("https://storage.googleapis.com/")


async def test_local_file_serves_bytes(tmp_path: Path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"\x00" * 64)
    app = make_app(StubLocator(LocalFile(f)))
    resp = await _get(app, "/api/media/123")
    assert resp.status_code == 200
    assert resp.headers["accept-ranges"] == "bytes"


async def test_local_file_range_request(tmp_path: Path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(bytes(range(100)))
    app = make_app(StubLocator(LocalFile(f)))
    resp = await _get(app, "/api/media/123", headers={"Range": "bytes=10-19"})
    assert resp.status_code == 206
    assert resp.content == bytes(range(10, 20))


async def test_miss_is_404():
    app = make_app(StubLocator(MediaNotAvailable(123, "local cache: x; ai store: y")))
    resp = await _get(app, "/api/media/123")
    assert resp.status_code == 404
    assert "local cache" in resp.text
```

- [ ] **Step 2: Run them — must fail**

Run: `.venv/bin/python -m pytest tests/unit/test_stream_media_locator.py -v`
Expected: FAIL — `StubLive` has `media_locator` but the route still calls `ctx.proxy_resolver`.

- [ ] **Step 3: Add the `media_locator` property to `LiveCtx`**

In `backend/app/context.py`, add to the imports section:

```python
from backend.app.services.media_locator import MediaLocator
```

and add this property on the `LiveCtx` class (next to the other delegation properties):

```python
    @property
    def media_locator(self) -> MediaLocator:
        """Playback byte-source chooser. Built per access (cheap: pure
        wiring); composition stays in the composition root, so the
        route never touches _gcs_service / ai_store directly."""
        return MediaLocator(
            proxy_resolver=self.proxy_resolver,
            ai_store=self.ai_store,
            gcs_service=self._gcs_service,
            prefer=self.core.settings.playback_source,
        )
```

- [ ] **Step 4: Rewrite the non-uploaded branch of `stream_media`**

In `backend/app/routes/media.py`: add imports

```python
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse

from backend.app.services.media_locator import LocalFile, MediaNotAvailable, RemoteUrl
```

and replace the `else:` branch of `stream_media` (currently lines 76–83, the `get_live_ctx` / `proxy_resolver` block) with:

```python
    else:
        ctx = get_live_ctx(request)
        try:
            located = await ctx.media_locator.locate(clip_id)
        except MediaNotAvailable as exc:
            raise HTTPException(404, str(exc)) from exc
        if isinstance(located, RemoteUrl):
            # Browser follows to GCS; range requests for seeking go
            # straight to the signed URL, bytes never transit this app.
            return RedirectResponse(located.url, status_code=307)
        path = located.path
```

(The `if ctx.proxy_resolver is None: raise HTTPException(503, ...)` guard is removed — the locator treats a missing resolver as a layer miss, and a both-miss is a 404, which is the truthful answer.)

- [ ] **Step 5: Run the new tests and the full suite**

Run: `.venv/bin/python -m pytest tests/unit/test_stream_media_locator.py -v && .venv/bin/python -m pytest && .venv/bin/lint-imports`
Expected: all PASS, import contracts green (locator import in routes is services-from-routes, which is allowed).

- [ ] **Step 6: Commit**

```bash
git add backend/app/context.py backend/app/routes/media.py tests/unit/test_stream_media_locator.py
git commit -m "stream_media: locate via MediaLocator; 307 to GCS signed URLs"
```

### Task 15: Cloud playback config + phase-4 acceptance

**Files:**
- Modify: `deploy/cloudrun.env.yaml`

- [ ] **Step 1: Add to `deploy/cloudrun.env.yaml`**

```yaml
# Phase 4: playback prefers GCS signed URLs (local disk is ephemeral).
PLAYBACK_SOURCE: "gcs"
```

- [ ] **Step 2: Commit and push**

```bash
git add deploy/cloudrun.env.yaml
git commit -m "Phase 4: cloud playback prefers GCS signed URLs"
```

- [ ] **Step 3 (operator): acceptance flow 5**

Spec flow 5 on the deployed service: GCS-backed clip plays via a 307 to `storage.googleapis.com` (check devtools network tab; seeks issue range requests against the signed URL); a clip in neither layer shows the prefetch affordance, and plays after prefetch; locally with `PLAYBACK_SOURCE=local` a cached clip still serves from disk (no redirect).

---

## Done criteria

All tasks committed; spec manual acceptance flows 1–5 pass (flow 6 belongs to deferred phase 5); `pytest` and `lint-imports` green; the deploy workflow green on `main`. Wrap up with the superpowers:finishing-a-development-branch skill — and per project CLAUDE.md, the ADR (Task 8) and decisions.md index must be in place before the session ends.
