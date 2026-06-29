# Deployment

AI Archive runs in two places: the developer's Mac (dev) and the CatDV
server (prod). The same code; only env vars differ.

> **Cloud Run** is the primary deployment. Its complete guide —
> one-time GCP setup, CI/CD pipeline, local-proxy access, and the
> local-vs-cloud env-var matrix — lives in
> [`../deploy/README.md`](../deploy/README.md) (spec:
> `docs/specs/2026-06-09-cloud-run-deployment-design.md`). The sections
> below describe the local Mac-dev and CatDV-server systemd deploys.

## Dev (Mac)

```bash
git clone <repo>
cd ai-archive
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env
# Edit .env: at minimum set CATDV_PASSWORD, GOOGLE_APPLICATION_CREDENTIALS,
# and INSTANCE_ID (mandatory; a lowercase slug unique to this machine,
# e.g. local-<yourname>). It namespaces uploaded-clip media in the shared
# GCS bucket so instances never overwrite each other (issue #55). The app
# refuses to boot if INSTANCE_ID is unset.
./run.sh
```

VPN to CatDV (`192.168.1.41`) must be up before starting.

### One-time GCP bootstrap (local dev)

Local dev needs a GCP project with the AI/storage APIs enabled, a
`catdv-annotator` service account, a proxy bucket, and a key JSON for
`GOOGLE_APPLICATION_CREDENTIALS`. This runs **once per project** (the
Cloud Run deployment uses project `catdav`; local dev can share it or use
a separate project). Each dev mints their own key from the shared SA.

```bash
export PROJECT_ID=<your-project>          # e.g. catdav
export REGION=europe-west3
export BUCKET=${PROJECT_ID}-proxies
export SA=catdv-annotator@${PROJECT_ID}.iam.gserviceaccount.com

# APIs
gcloud services enable aiplatform.googleapis.com storage.googleapis.com \
  secretmanager.googleapis.com iamcredentials.googleapis.com --project=$PROJECT_ID

# Bucket + service account (idempotent — skip if they exist)
gsutil mb -p $PROJECT_ID -l $REGION gs://$BUCKET
gcloud iam service-accounts create catdv-annotator \
  --display-name="AI Archive" --project=$PROJECT_ID

# Roles: write the proxy bucket, call Vertex AI
gsutil iam ch serviceAccount:${SA}:objectAdmin gs://$BUCKET
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member=serviceAccount:$SA --role=roles/aiplatform.user

# Per-dev key JSON for GOOGLE_APPLICATION_CREDENTIALS (cap: 10 keys/SA)
mkdir -p ~/.gcp
gcloud iam service-accounts keys create ~/.gcp/catdv-annotator-key.json \
  --iam-account=$SA --project=$PROJECT_ID
echo "GOOGLE_APPLICATION_CREDENTIALS=$HOME/.gcp/catdv-annotator-key.json" >> .env
```

Keys are sensitive: keep them outside the repo, `chmod 600`, never
commit. Cloud Run does **not** use these keys — it authenticates as the
runtime SA via Application Default Credentials. To enable the optional
Gemini Live assistant, run `deploy/enable-gemini-live.sh` (see the
top-level `README.md`).

## Prod (CatDV server)

### Prerequisites (one-time, requires admin access to the CatDV server)

1. **Linux user** with read access to the CatDV proxy directory (typically a
   member of the group owning `/usr/local/catdvServer/<proxies>`). Talk to Honza
   for the exact path and group.
2. **`python3.12` available** (or higher).
3. **Outbound HTTPS** to `*.googleapis.com` (Vertex AI + GCS) — confirm before
   deploying.

### Deploy

```bash
# As the service user, in /opt/ai-archive
git clone <repo> .
python3 -m venv .venv
.venv/bin/pip install -e .
cp .env.example .env
# Edit .env:
#   APP_ENV=prod
#   CATDV_BASE_URL=http://localhost:8080
#   PROXY_SOURCE=filesystem
#   PROXY_FS_ROOT=/usr/local/catdvServer/<proxies>
#   PROXY_PATH_TEMPLATE="{root}/{clip_id}.mov"   # confirm with Honza
#   GOOGLE_APPLICATION_CREDENTIALS=/etc/catdv-annotator/sa.json
#   INSTANCE_ID=<unique-slug>   # mandatory; e.g. "prod" — namespaces uploads (issue #55)
# CATDV_USERNAME / CATDV_PASSWORD come from Secret Manager (do NOT set in .env)
sudo cp deploy/catdv-annotator.service /etc/systemd/system/
sudo systemctl enable --now catdv-annotator
```

### Confirming health

```bash
curl -s http://localhost:8765/api/health
# {"status":"ok"}

curl -s http://localhost:8765/api/templates
# []   (or seeded templates)
```

Logs:

```bash
journalctl -u catdv-annotator -f
```

### Rolling out a new version

```bash
cd /opt/catdv-annotator
sudo -u catdv git pull
sudo -u catdv .venv/bin/pip install -e .
sudo systemctl restart catdv-annotator
```

## Filesystem archive provider

To run against a plain directory of media files instead of CatDV, set:

```bash
ARCHIVE_PROVIDER=fs
FS_ROOT=/path/to/archive/root
# Optional; comma-separated; default covers the common video extensions
FS_MEDIA_EXTS=.mov,.mp4,.mkv,.mxf,.m4v,.avi
```

When `ARCHIVE_PROVIDER=fs`:

- `CATDV_BASE_URL`, `CATDV_USERNAME`, `CATDV_PASSWORD` are ignored
  (no CatDV client is constructed).
- `PROXY_*` settings are also ignored — `media_is_local=True` so no
  proxies are copied; the workspace manager skips the media leg.
- `GCP_*` / `GCS_BUCKET_NAME` / `GOOGLE_APPLICATION_CREDENTIALS` are
  still required for AI annotation (Gemini still needs an upload).
- Field definitions live in `FS_ROOT/.archive/fields.json` (optional).
- Per-clip annotations are persisted as `<clip>.annot.json` sidecars
  next to the media. Writes are POSIX-atomic.

See `docs/fs-archive-format.md` for the directory layout, sidecar JSON
schema, and etag semantics.

## Running on the CatDV host (no proxy cache)

When the annotator runs on the same machine as the CatDV server, set:

```
PROXY_SOURCE=filesystem
```

…and ensure the OS user has **read access** to every directory listed
under `mediaType: proxy, target: web` in `GET /catdv/api/9/mediastores`.
For this installation that's `/Volumes/ARECA/CatDV_Proxy/` and
`/Volumes/ARECA2/CatDV_Proxy/`.

No other settings change. At startup the app fetches the media-store
config and builds the hires→proxy mapping. Per clip, it reads
`media.filePath` from CatDV, swaps the hires-root prefix for the
matching proxy root, and hands the resulting path to Gemini ingestion.

**What this turns off:** the `data/cache/proxies/` directory is no
longer written. `proxy_cache` rows are not recorded. CatDV doesn't get
hit for proxy bytes — only for clip metadata (which is light, already
cached).

**Failure modes:**

- `ProxyNotFound: ... no media.filePath` — the clip has no media
  attached upstream. Same outcome as the REST resolver would have had.
- `ProxyNotFound: ... no mediastore rule` — the clip's `media.filePath`
  prefix isn't in any media-store. Re-check `/mediastores` and confirm
  the volume mount you expect is present.
- `ProxyNotFound: ... not on disk` — the file is missing or the LTO
  archive has reclaimed it. CatDV's web client would show the same
  "media unavailable" state for that clip.

There is intentionally no automatic fallback to the REST resolver
when a proxy is missing on disk — failing loudly is better than
silently re-introducing the cache + VPN dependency.

## Offline fallback (no CatDV at all)

Two ways the app degrades to offline:

1. **Forced** — set `CATDV_OFFLINE=true` in `.env`. The app skips the
   CatDV login at startup (no seat is taken), uses the cached clip
   list from SQLite, serves only proxies already on disk, and refuses
   manual reconnects. The header chip is red. Useful when the VPN is
   known to be down.
2. **Auto** — with `CATDV_OFFLINE` unset, the app boots normally but
   catches connection failures at startup and during the periodic
   health probe. It then halts the probe loop, swaps the proxy
   resolver to the cache-only variant for the rest of the session, and
   shows a yellow "Offline — click to reconnect" chip. Clicking the
   chip issues `POST /api/connection/retry`, which runs a single
   probe; on success the loop resumes.

**Writes while offline**: change-sets that the adapter would normally
push to CatDV are queued by the existing `WriteQueue` (the adapter's
`apply_changes` raises `RetryableError` when offline). They flush in
order when the app is back online.

**Reconnect**: the user clicks the chip — there is no background
re-probing. This is intentional: a stuck app retrying CatDV every 30s
without a working VPN would just generate noise and could hold a seat
if the network flapped briefly.

**What this turns off in the UI**: Annotate, "Cache locally", and
"Refresh from CatDV" actions are hidden; clip-detail pages for
un-cached clips render a 404-style "not available offline" page
instead of erroring.

