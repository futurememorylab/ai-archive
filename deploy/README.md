# Cloud Run deployment

Canonical reference for running AI Archive on Google Cloud Run. Covers the
one-time GCP setup, the CI/CD pipeline, how to reach the private service
through a local proxy, and the local-vs-cloud environment variables.

> **Note:** the GCP project, Cloud Run service, and related infrastructure
> still use the legacy `catdv-annotator` slug. These are deployed resources
> and renaming them is not worth the operational risk.

- **Design spec:** `docs/specs/2026-06-09-cloud-run-deployment-design.md`
- **Decisions:** ADRs 0066–0076 (see `docs/decisions.md`)
- **Local / on-prem deploys** (Mac dev, CatDV-server systemd): `docs/DEPLOY.md`

## At a glance

| | |
|---|---|
| Project | `catdav` |
| Region | `europe-west3` |
| Service | `catdv-annotator` (Cloud Run, **private** — `--no-allow-unauthenticated`) |
| Image | `europe-west3-docker.pkg.dev/catdav/catdv-annotator/app` |
| Runtime SA | `catdv-annotator@catdav.iam.gserviceaccount.com` |
| Deployer SA | `github-deployer@catdav.iam.gserviceaccount.com` (used by CI via WIF) |
| Instances | pinned `min=max=1`, `--no-cpu-throttling` — **load-bearing, never change** |
| Media bucket | `gs://catdv-proxies` (AI-store proxy cache) |
| DB bucket | `gs://catdv-annotator-db` (Litestream replica of SQLite) |

The single-instance pin is correctness, not cost: **one** CatDV license
seat, **one** Litestream writer, **one** in-process write queue. Never
raise the instance count and never traffic-split (rolling deploys are
fine). See ADR 0066.

## One-time GCP setup

Run once by an operator with `gcloud` auth (Owner/Editor) on project
`catdav`. Idempotent steps are marked; safe to re-run. Day-to-day deploys
are the GitHub Actions workflow — you do **not** re-run this for a normal
release.

```bash
export PROJECT_ID=catdav
export REGION=europe-west3
export REPO=catdv-annotator
export RUNTIME_SA=catdv-annotator@${PROJECT_ID}.iam.gserviceaccount.com
export DEPLOYER_SA=github-deployer@${PROJECT_ID}.iam.gserviceaccount.com

# 1. Enable APIs
gcloud services enable \
  run.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com iamcredentials.googleapis.com \
  storage.googleapis.com aiplatform.googleapis.com \
  --project=$PROJECT_ID

# 2. Artifact Registry (Docker images)
gcloud artifacts repositories create $REPO --repository-format=docker \
  --location=$REGION --project=$PROJECT_ID

# 3. Buckets
gsutil mb -p $PROJECT_ID -l $REGION gs://catdv-proxies        # AI-store media cache
gsutil mb -p $PROJECT_ID -l $REGION gs://catdv-annotator-db   # Litestream replica

# 4. Runtime service account + roles
gcloud iam service-accounts create catdv-annotator --project=$PROJECT_ID
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member=serviceAccount:$RUNTIME_SA --role=roles/aiplatform.user   # Vertex Gemini
gsutil iam ch serviceAccount:${RUNTIME_SA}:roles/storage.objectAdmin gs://catdv-proxies
gsutil iam ch serviceAccount:${RUNTIME_SA}:roles/storage.objectAdmin gs://catdv-annotator-db
# Signed URLs are minted via IAM signBlob: the SA signs as itself.
gcloud iam service-accounts add-iam-policy-binding $RUNTIME_SA \
  --member=serviceAccount:$RUNTIME_SA \
  --role=roles/iam.serviceAccountTokenCreator --project=$PROJECT_ID

# 5. Secrets (values piped from stdin — never on the command line)
printf '%s' "$CATDV_PASSWORD_VALUE" | gcloud secrets create catdv-password \
  --data-file=- --replication-policy=automatic --project=$PROJECT_ID
printf '%s' "$GEMINI_API_KEY_VALUE" | gcloud secrets create gemini-api-key \
  --data-file=- --replication-policy=automatic --project=$PROJECT_ID
# wg-private-key is created in the WireGuard section below.
for s in catdv-password gemini-api-key; do
  gcloud secrets add-iam-policy-binding $s \
    --member=serviceAccount:$RUNTIME_SA \
    --role=roles/secretmanager.secretAccessor --project=$PROJECT_ID
done

# 6. Workload Identity Federation for GitHub Actions (no long-lived SA keys)
gcloud iam workload-identity-pools create github --location=global \
  --project=$PROJECT_ID
gcloud iam workload-identity-pools providers create-oidc github-oidc \
  --location=global --workload-identity-pool=github \
  --issuer-uri=https://token.actions.githubusercontent.com \
  --attribute-mapping=google.subject=assertion.sub,attribute.repository=assertion.repository \
  --attribute-condition="assertion.repository=='futurememorylab/ai-archive'" \
  --project=$PROJECT_ID
gcloud iam service-accounts create github-deployer --project=$PROJECT_ID
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
gcloud iam service-accounts add-iam-policy-binding $DEPLOYER_SA \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/attribute.repository/futurememorylab/ai-archive" \
  --role=roles/iam.workloadIdentityUser --project=$PROJECT_ID
for role in roles/run.admin roles/artifactregistry.writer roles/iam.serviceAccountUser; do
  gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member=serviceAccount:$DEPLOYER_SA --role=$role
done

# 7. GitHub repo secrets (Settings → Secrets and variables → Actions)
#    GCP_WIF_PROVIDER = projects/<PROJECT_NUMBER>/locations/global/workloadIdentityPools/github/providers/github-oidc
#    GCP_DEPLOYER_SA  = github-deployer@catdav.iam.gserviceaccount.com

# 8. Run the first deploy (push to main, or trigger the workflow manually),
#    THEN grant run.invoker — these target the service, so it must exist.
gcloud run services add-iam-policy-binding catdv-annotator --region=$REGION \
  --member=serviceAccount:$DEPLOYER_SA --role=roles/run.invoker \
  --project=$PROJECT_ID   # CI's health-check step calls the private URL
gcloud run services add-iam-policy-binding catdv-annotator --region=$REGION \
  --member=user:peter.hora@gmail.com --role=roles/run.invoker \
  --project=$PROJECT_ID   # operator access via the local proxy
```

### WireGuard tunnel (onetun → CatDV)

The app reaches CatDV (`192.168.1.41`) over a WireGuard tunnel to the
office gateway, run in-process by `VpnSupervisor` (ADR 0075) using the
`onetun` binary baked into the image (ADR 0067). Generate a key on a
trusted machine and register the peer on the office WireGuard server:

```bash
wg genkey | tee /tmp/wg-cloud.key | wg pubkey   # private → file, public → stdout
```

On the office WG server, add this public key as a peer with
`AllowedIPs = <WG_SOURCE_IP>/32`, and scope the cloud peer's allowed
route to only `192.168.1.41/32` (least privilege — CatDV, not the whole
LAN). Then store the private key as a secret:

```bash
gcloud secrets create wg-private-key --data-file=/tmp/wg-cloud.key \
  --replication-policy=automatic --project=$PROJECT_ID
gcloud secrets add-iam-policy-binding wg-private-key \
  --member=serviceAccount:$RUNTIME_SA \
  --role=roles/secretmanager.secretAccessor --project=$PROJECT_ID
shred -u /tmp/wg-cloud.key
```

Fill `WG_ENDPOINT` / `WG_PEER_PUBKEY` / `WG_SOURCE_IP` in
`deploy/cloudrun.env.yaml` from the office server's public host:port, its
public key, and the tunnel IP assigned to the new peer.

> **Note (current state):** the deployed config reuses an existing
> personal peer key, so the cloud tunnel and that Mac's tunnel cannot be
> up at the same time (one endpoint per peer key). A dedicated
> least-privilege cloud peer is the eventual hardening.

## CI/CD pipeline

Source: [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml).
Triggers on **push to `main`** and on manual **workflow_dispatch**.

```
push to main ─▶ test ─▶ deploy ─▶ verify /api/health
               │        │
               │        └─ WIF auth → build/push image → gcloud run deploy
               └─ pytest + lint-imports (import-linter contracts)
```

1. **test** — `pip install -e ".[dev]"`, `python -m pytest`, `lint-imports`.
   The deploy job `needs: test`, so a red test/contract blocks the release.
2. **deploy** — authenticates to GCP via **Workload Identity Federation**
   (keyless; no SA JSON in GitHub), then:
   - `docker build --platform linux/amd64` and push to Artifact Registry,
     tagged with both the commit SHA and `latest`.
   - `gcloud run deploy` with the load-bearing flags
     `--min-instances=1 --max-instances=1 --no-cpu-throttling`,
     non-secret config from `--env-vars-file=deploy/cloudrun.env.yaml`, and
     secrets from `--set-secrets` (`catdv-password`, `gemini-api-key`,
     `wg-private-key` from Secret Manager).
3. **verify** — fetches the service URL, mints an identity token, and
   `curl -fsS` the private `/api/health` endpoint. A non-200 fails the run.

### Manual deploy / rollback

A normal release is just a push to `main`. To deploy without a push,
trigger the workflow from the Actions tab (**Run workflow**). To roll
back, re-deploy a known-good image by SHA (no rebuild needed):

```bash
gcloud run deploy catdv-annotator --region europe-west3 \
  --image europe-west3-docker.pkg.dev/catdav/catdv-annotator/app:<good-sha>
```

Image tags are immutable per SHA, so any prior `:<sha>` is a valid
rollback target. List recent revisions with
`gcloud run revisions list --service catdv-annotator --region europe-west3`.

## Accessing the deployed app (local proxy)

The service is **private** (`--no-allow-unauthenticated`); hitting its
`*.run.app` URL directly in a browser returns **403**. Access goes through
the `gcloud` proxy, which injects your identity token:

```bash
gcloud auth login        # once, as a user with roles/run.invoker on the service
gcloud run services proxy catdv-annotator --region europe-west3
# leave it running, then open:
open http://localhost:8080
```

`gcloud run services proxy` listens on `localhost:8080` and forwards each
request with a fresh ID token for your account — no VPN, no IAP, no
service key. If you get 403, you're missing `roles/run.invoker` (granted
in step 8 of the one-time setup).

## IAP access control (who can sign in)

Cloud Run IAP fronts all ingress in prod (`AUTH_BACKEND=iap`). The split:

- **IAP authenticates.** Google forces a login at the edge and injects a signed
  assertion the app verifies (`auth/adapters/iap.py`). It cannot fail open.
- **The app authorizes.** The default-deny gate (`main.py`) + the `user_roles`
  table decide who is actually admitted and as what role. *Reaching the edge ≠
  being let in.*

**The edge is bound to `allAuthenticatedUsers`** — any signed-in Google account
can REACH the app; the app's denial page + admin approval decide the rest. This
is what lets you manage users entirely from `/admin` → Access & Permissions with
no console step (ADR 0113). Grant it once per service:

```bash
gcloud iap web add-iam-policy-binding --resource-type=cloud-run \
  --service=catdv-annotator --region=europe-west3 \
  --member=allAuthenticatedUsers --role=roles/iap.httpsResourceAccessor
```

🚫 **Never `allUsers`.** That is the anonymous public internet;
`allAuthenticatedUsers` requires a real Google login. One word apart, opposite
security posture — double-check the member before you Save.

**Org policy:** if the binding is rejected with an
`iam.allowedPolicyMemberDomains` / domain-restricted-sharing error, the org
policy forbids `allAuthenticatedUsers` and this model isn't deployable as-is.

**Adding / removing users is app-side only:** `/admin` → Access & Permissions →
**Add member** (or **Accept** a pending request) to add; **Revoke** to remove.
No per-user `gcloud`/console step. Deploy-time owners come from `ADMIN_EMAILS`.

**Reverting to a per-user allowlist** (if ever needed): remove the
`allAuthenticatedUsers` binding and instead grant
`roles/iap.httpsResourceAccessor` to each `user:<email>`. The app keeps working;
only the set of people who can reach the edge narrows.

## Local vs cloud environment variables

Source of truth for names, types, and defaults:
[`backend/app/settings.py`](../backend/app/settings.py). Local dev reads
`.env` (see [`.env.example`](../.env.example), git-ignored, holds
secrets). Cloud reads **non-secrets** from the committed
[`deploy/cloudrun.env.yaml`](cloudrun.env.yaml) and **secrets** from
Secret Manager via `--set-secrets`. `DB_PATH` and `LITESTREAM_REPLICA_URL`
are read by `entrypoint.sh` / `litestream.yml`, not by pydantic Settings.

### Differs between local and cloud

| Variable | Local (`.env`) | Cloud (`cloudrun.env.yaml`) | Notes |
|---|---|---|---|
| `APP_ENV` | `dev` | `prod` | |
| `INSTANCE_ID` | `local-dev` / per-dev | `prod` (staging: `staging`) | **Mandatory everywhere** (not cloud-only). Lowercase slug `[a-z0-9-]` unique per running instance. Namespaces uploaded-clip GCS keys (`instances/{INSTANCE_ID}/uploads/{clip_id}.mov`) so instances sharing the `catdv-proxies` bucket cannot overwrite each other's uploads; CatDV clips stay shared at `clips/{clip_id}.mov`. App fails to boot if unset. See issue #55 + `docs/superpowers/specs/2026-06-15-uploads-multi-instance-storage-design.md`. |
| `DATA_DIR` | `./data` | `/data` | Cloud disk is **ephemeral** — durability comes from Litestream + GCS. |
| `CATDV_BASE_URL` | `http://192.168.1.41:8080` | `http://127.0.0.1:18080` | Local: direct LAN. Cloud: onetun local forward into the WG tunnel. |
| `MEDIA_CACHE` | `local` | `ai_store` | Local: disk proxy cache, GCS fallback. Cloud: GCS-only + signed-URL playback (ADR 0069). |
| `GCP_LOCATION` | `europe-west3` | `global` | Vertex endpoint differs; do not "fix" without checking model availability. |
| `CATDV_CONNECT_MODE` | unset → `manual` (default) | `manual` | Same effective value; cloud sets it explicitly (always-on instance must not hold a seat 24/7 — ADR 0068). |

### Local-only (ignored / unset in cloud)

| Variable | Purpose |
|---|---|
| `PROXY_SOURCE` | `rest` vs `filesystem` proxy locator; cloud uses the AI store, so this is moot. |
| `PROXY_CACHE_CAP_GB` | Local proxy-cache size cap; no local cache in cloud. |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to a local SA-key JSON. Cloud uses Application Default Credentials (the runtime SA) — never set this in cloud. |

### Cloud-only (set in `cloudrun.env.yaml` / Secret Manager, never in `.env`)

| Variable | Source | Purpose |
|---|---|---|
| `WG_ENDPOINT` / `WG_PEER_PUBKEY` / `WG_SOURCE_IP` | `cloudrun.env.yaml` | WireGuard peer config; all-present gates the VPN feature (`settings.vpn_managed`). |
| `WG_PRIVATE_KEY` | Secret Manager `wg-private-key` | WireGuard private key (secret). |
| `ONETUN_MTU` | `cloudrun.env.yaml` | `1000` — verified to clear the Cloud Run→gateway path MTU; `1380` black-holed outbound writeback. **Load-bearing**, see ADR 0076. |
| `LITESTREAM_REPLICA_URL` | `cloudrun.env.yaml` | `gcs://catdv-annotator-db/litestream`; when set, `entrypoint.sh` restores + replicates SQLite. |
| `DB_PATH` | `cloudrun.env.yaml` | `/data/app.db`; must equal `settings.data_dir / "app.db"`. Read by entrypoint/Litestream. |

### Secrets (cloud)

Injected via `--set-secrets` from Secret Manager — never in
`cloudrun.env.yaml`, never in git:

| Env var | Secret Manager id |
|---|---|
| `CATDV_PASSWORD` | `catdv-password` |
| `GEMINI_API_KEY` | `gemini-api-key` |
| `WG_PRIVATE_KEY` | `wg-private-key` |

Locally these live in `.env` (`CATDV_PASSWORD`, optional `GEMINI_API_KEY`);
there is no local WireGuard, so `WG_PRIVATE_KEY` stays unset.

## Staging (`catdv-annotator-staging`)

A persistent, scale-to-zero **second** Cloud Run service for testing cloud + IAP
behaviour from any branch **without pushing to `main`** and **without touching
production**. The image is built from the current working tree via Cloud Build
(no local Docker). Config: [`deploy/staging.env.yaml`](staging.env.yaml);
deploy: [`deploy/deploy-staging.sh`](deploy-staging.sh); agent path: the
`deploy-staging` skill.

```bash
gcloud auth login            # run as a project Owner, NOT the runtime SA
./deploy/deploy-staging.sh   # build current tree → deploy to staging
./deploy/deploy-staging.sh --init-iap   # first deploy only: enable IAP + invoker grant
# then grant testers:
gcloud iap web add-iam-policy-binding --resource-type=cloud-run \
  --service=catdv-annotator-staging --region=europe-west3 \
  --member=user:YOU@example.com --role=roles/iap.httpsResourceAccessor
```

**Isolation from prod (do not break — these protect production):**

- `CATDV_OFFLINE=true` — CatDV's single *global* license seat must not be
  contended. Staging can't test live CatDV reads/writes; do those locally.
- **No `LITESTREAM_REPLICA_URL`** — staging uses an ephemeral DB (resets on cold
  start). It must never point at prod's `gs://catdv-annotator-db` path: two
  Litestream writers on one path corrupts the DB.
- Don't run jobs on staging that write blobs into prod's `gs://catdv-proxies`.

**Cost:** scales to zero → ~nothing when idle; per-deploy = one Cloud Build
(same as a `main` deploy) + a few cents of image storage.

**IAP audience:** the direct-Cloud-Run-IAP JWT audience is not authoritatively
documented, so it is *discovered* from a live token on staging (sign in with
`AUTH_BACKEND=iap` + `IAP_AUDIENCE` unset, read the token's `aud`), then set in
`staging.env.yaml`. See ADR 0084.

**Teardown:** `gcloud run services delete catdv-annotator-staging --region europe-west3`.
