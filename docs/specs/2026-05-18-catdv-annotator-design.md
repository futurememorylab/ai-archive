# CatDV Annotator — Design Spec

**Date:** 2026-05-18
**Status:** Draft, awaiting review
**Author:** Peter Hora (with Claude)

---

## 1. Overview

A standalone local-first web app for the Pragafilm CatDV archive that combines two interfaces into one:

1. **Read+edit access to CatDV** clips, metadata, markers, and custom `pragafilm.*` fields, over the existing CatDV REST API.
2. **AI annotation engine** that batch-sends video clips to Google Gemini (Vertex AI) with reusable prompt templates, then writes the structured results back to CatDV after human review.

The same SQLite database also serves as the long-term **annotation archive** — every Gemini response, prompt, and clip snapshot is preserved — so a future AI-curation/search app can layer richer search and semantic retrieval on top of CatDV without changing CatDV itself.

### Why this exists

- CatDV is the source of truth for media + canonical metadata, but its full-text search is weak and it has no AI annotation primitives.
- The current workflow forces switching between the CatDV web client and any AI tooling, making batch annotation tedious.
- The Pragafilm archive is large enough (~10k clips, 1920s–30s home-movie footage) that human-only annotation is impractical.

### Concrete user (v1)

- One person (`klientAI` in CatDV's `ai` group, on a Mac in dev; eventually the same code runs on the Pragafilm CatDV server itself).
- Reads/writes a single catalog: **`881507` "AI katalog"**.
- Drives batch annotation jobs interactively.

### Out-of-scope explicitly

- Multi-user, sharing, real authentication.
- A dedicated search/curation app — the schema reserves slots for it; the app itself is a separate project.
- Catalog management UI (browse only).
- Marker/metadata editing outside of the annotation review flow (browse-and-view only outside review).
- CatDV field-definition creation (admin-only on the server, done by Honza when needed).
- Desktop bundling (Electron/Tauri).

---

## 2. High-level architecture

```
┌──────────────────────────────────────────────────────────┐
│ Browser at localhost:8765                                │
│   Two-pane UI rendered server-side via Jinja2            │
│     • CatDV pane: clip browser, search, clip detail      │
│     • Annotation pane: templates, jobs, review board     │
│   Interactivity: HTMX + Alpine.js                        │
│   Video review: vanilla JS player widget (player.js)     │
└────────────────────────────┬─────────────────────────────┘
                             │ HTTP (HTML fragments, SSE)
┌────────────────────────────▼─────────────────────────────┐
│ FastAPI app (localhost:8765, Uvicorn)                    │
│                                                          │
│  routes/         services/                               │
│   • catdv         • catdv_client     (REST wrapper)      │
│   • templates     • gemini_client    (Vertex AI)         │
│   • jobs          • gcs_client       (GCS uploads)       │
│   • review        • proxy_resolver   (rest|filesystem)   │
│   • media         • annotator        (queue, templates,  │
│   • events (SSE)                       review state,     │
│                                        write-back)       │
│                                                          │
│   AppContext singleton wired at startup                  │
│   Single asyncio worker task per job                     │
└──────────┬──────────────────────────┬─────────┬──────────┘
           │                          │         │
           │ SQLite (DATA_DIR/app.db) │         │
           │ + DATA_DIR/cache/proxies/│         │
           │   (rest mode only)       │         │
           │                          │         │
┌──────────▼──────────────────────────┼─────────┼──────────┐
│ Local filesystem                    │         │          │
└─────────────────────────────────────┼─────────┼──────────┘
                                      │         │
                          CatDV REST  │         │  GCS upload + Vertex AI
                          (VPN in dev,│         │  (europe-west3)
                          loopback    │         │
                          in prod)    │         │
┌─────────────────────────────────────▼─┐  ┌────▼──────────────────────┐
│ CatDV server (192.168.1.41:8080)      │  │ GCS bucket                │
│   /catdv/api/9/...                    │  │   gs://<bucket>/clips/    │
│                                       │  │                           │
│ Prod adds: read access to proxy files │  │ Vertex AI reads gs:// URI │
│ on local disk under PROXY_FS_ROOT     │  │ directly via SA permission│
└───────────────────────────────────────┘  └───────────────────────────┘
```

### Three runtime concerns the architecture must absorb

1. **Slow VPN to CatDV in dev (~370 KB/s).** All CatDV calls go through one authenticated `catdv_client` session; `AUTH` responses trigger transparent re-login; proxy downloads use `Range` for resumable streaming. The same module works at native speed over loopback in prod.

2. **Long Gemini jobs.** Each batch is a background job: resolve proxy (download or filesystem) → upload to GCS once → call Vertex with `gs://` URI and structured-output schema → store annotation + parsed result. Browser subscribes via Server-Sent Events; closing the tab does not stop the job.

3. **Write-back is gated by review.** Gemini's output never reaches CatDV directly. It lands in `review_items` as proposed changes. The Review pane shows each item against the actual footage, lets the user accept/edit/reject, and applies per-clip in a single `PUT` that merges with current CatDV state.

### AppContext (DI)

A single `AppContext` dataclass is constructed at startup and provided to routes via FastAPI `Depends`. Holds: settings, DB connection pool, `catdv_client`, `gemini_client`, `gcs_client`, `proxy_resolver`, the job worker handle. Services are singletons within the process. This is a direct response to the PoC's "each route instantiates services on import" pattern.

---

## 3. Data model (SQLite)

One file: `DATA_DIR/app.db`. Three logical layers — catalogues, runtime, archive — all in one schema for simplicity. Migrations are hand-written SQL in `backend/migrations/NNNN_*.sql`, applied in order at startup.

### Catalogues

```sql
-- Saved annotation "recipes"
CREATE TABLE templates (
  id              INTEGER PRIMARY KEY,
  name            TEXT NOT NULL UNIQUE,
  description     TEXT,
  prompt          TEXT NOT NULL,           -- text sent to Gemini
  output_schema   TEXT NOT NULL,           -- JSON Schema for structured output
  target_map      TEXT NOT NULL,           -- JSON: schema field -> CatDV target
  model           TEXT NOT NULL,           -- e.g. "gemini-2.5-pro"
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL,
  archived        INTEGER NOT NULL DEFAULT 0
);
```

`target_map` example for a "scene markers + era" template:
```json
{
  "scenes":     {"kind": "markers"},
  "summary_cz": {"kind": "note",  "target": "pragafilm.popis.materialu", "mode": "append"},
  "decade":     {"kind": "field", "identifier": "pragafilm.dekáda.natočení"},
  "years":      {"kind": "field", "identifier": "pragafilm.rok.natočení"}
}
```

`kind` is one of:
- `markers` — schema field is an array of `{name, in, out, description?, category?, color?}`.
- `note` — schema field is a string; `target` selects which CatDV note field (`notes`, `bigNotes`, or a `pragafilm.*` text field); `mode` is `append` (default) or `replace`.
- `field` — schema field is a scalar or array of scalars; `identifier` is the CatDV custom field identifier.

### Runtime

```sql
CREATE TABLE jobs (
  id              INTEGER PRIMARY KEY,
  template_id     INTEGER NOT NULL REFERENCES templates(id),
  status          TEXT NOT NULL,           -- pending|running|completed|failed|cancelled
  created_at      TEXT NOT NULL,
  started_at      TEXT,
  finished_at     TEXT,
  total_clips     INTEGER NOT NULL,
  notes           TEXT
);

CREATE TABLE job_items (
  id              INTEGER PRIMARY KEY,
  job_id          INTEGER NOT NULL REFERENCES jobs(id),
  catdv_clip_id   INTEGER NOT NULL,
  status          TEXT NOT NULL,           -- pending|resolving|uploading|prompting|
                                           --  annotated|review_ready|applied|rejected|error
  error_message   TEXT,
  annotation_id   INTEGER REFERENCES annotations(id),
  started_at      TEXT,
  finished_at     TEXT
);

-- Local proxy file cache (rest mode only; bypassed in filesystem mode)
CREATE TABLE proxy_cache (
  catdv_clip_id   INTEGER PRIMARY KEY,
  file_path       TEXT NOT NULL,           -- relative to DATA_DIR
  size_bytes      INTEGER NOT NULL,
  etag            TEXT,
  downloaded_at   TEXT NOT NULL,
  last_used_at    TEXT NOT NULL
);

-- GCS uploads (reused across re-annotation)
CREATE TABLE gcs_files (
  catdv_clip_id   INTEGER PRIMARY KEY,
  gcs_uri         TEXT NOT NULL,           -- gs://<bucket>/clips/{id}.mov
  mime_type       TEXT NOT NULL,           -- video/quicktime
  size_bytes      INTEGER NOT NULL,
  sha256          TEXT NOT NULL,           -- of local source file
  uploaded_at     TEXT NOT NULL,
  last_used_at    TEXT NOT NULL
);
```

### Archive (the long-term annotation store + future search substrate)

```sql
-- The permanent annotation record. One row per (clip, template, run).
CREATE TABLE annotations (
  id                 INTEGER PRIMARY KEY,
  catdv_clip_id      INTEGER NOT NULL,
  catdv_clip_name    TEXT NOT NULL,        -- denormalized for search
  template_id        INTEGER NOT NULL REFERENCES templates(id),
  job_id             INTEGER REFERENCES jobs(id),
  model              TEXT NOT NULL,
  prompt_used        TEXT NOT NULL,        -- snapshot of template.prompt at run time
  raw_response       TEXT NOT NULL,        -- full Gemini response JSON
  structured_output  TEXT NOT NULL,        -- parsed JSON matching output_schema
  clip_snapshot      TEXT NOT NULL,        -- full CatDV clip JSON at annotation time
  created_at         TEXT NOT NULL
);
CREATE INDEX idx_annotations_clip ON annotations(catdv_clip_id);
CREATE INDEX idx_annotations_template ON annotations(template_id);

-- Free-text search over annotation content (Czech diacritics handled)
CREATE VIRTUAL TABLE annotations_fts USING fts5(
  clip_name, prompt_used, structured_output, raw_response,
  content='annotations', content_rowid='id',
  tokenize = 'unicode61 remove_diacritics 2'
);

-- Proposed changes to CatDV, derived from annotation + target_map
CREATE TABLE review_items (
  id                 INTEGER PRIMARY KEY,
  annotation_id      INTEGER NOT NULL REFERENCES annotations(id),
  catdv_clip_id      INTEGER NOT NULL,
  kind               TEXT NOT NULL,        -- marker|note|field
  target_identifier  TEXT,                 -- field identifier for kind=field
  proposed_value     TEXT NOT NULL,        -- JSON
  edited_value       TEXT,                 -- if user edited before applying
  decision           TEXT NOT NULL,        -- pending|accepted|rejected
  decided_at         TEXT,
  applied_at         TEXT                  -- when written to CatDV
);

-- Audit log of writes back to CatDV
CREATE TABLE write_log (
  id              INTEGER PRIMARY KEY,
  catdv_clip_id   INTEGER NOT NULL,
  annotation_id   INTEGER REFERENCES annotations(id),
  payload         TEXT NOT NULL,           -- exact JSON sent
  response        TEXT NOT NULL,           -- CatDV response envelope
  status          TEXT NOT NULL,           -- ok|error
  written_at      TEXT NOT NULL
);

-- Reserved for the future search/curation app (not populated in v1)
CREATE TABLE embeddings (
  annotation_id   INTEGER PRIMARY KEY REFERENCES annotations(id),
  model           TEXT NOT NULL,
  vector          BLOB NOT NULL,
  created_at      TEXT NOT NULL
);

CREATE TABLE tags (
  annotation_id   INTEGER NOT NULL REFERENCES annotations(id),
  tag             TEXT NOT NULL,
  source          TEXT NOT NULL,           -- 'user' | 'ai-curator'
  created_at      TEXT NOT NULL,
  PRIMARY KEY (annotation_id, tag)
);
```

### Schema design decisions

- **`clip_snapshot` per annotation.** CatDV state drifts; we need a frozen reference for what the model saw. ~19 KB JSON × N clips is cheap.
- **`raw_response` always kept.** A future curation pass may extract data we didn't parse today. Never throw raw away.
- **`prompt_used` snapshotted, not referenced.** Templates evolve; we need to know the exact prompt that produced this annotation.
- **FTS5 with `remove_diacritics 2`** — handles Czech `č/ř/š/ž`.
- **`embeddings` and `tags` reserved but empty** — schema ready when the search app starts; this app doesn't touch them.
- **No CatDV mirror tables.** CatDV stays the source of truth for media + canonical metadata.

---

## 4. Data flow

### 4.1 Batch annotation lifecycle (happy path)

```
1. User multi-selects clips in CatDV pane.
2. User picks template, clicks "Annotate N selected".
   → POST /api/jobs creates jobs row + N job_items (status=pending).
3. Worker loop (one task per job, processes items serially):

   for each job_item:
     a. status=resolving
        proxy_resolver.path_for(clip) → local Path
          rest mode:  cache hit?  yes → use cached path
                                  no  → stream-download via CatDV REST with Range,
                                        cache to DATA_DIR/cache/proxies/{id}.mov,
                                        insert proxy_cache row
          fs mode:    look up clip's filesystem path under PROXY_FS_ROOT,
                      stat, return directly (no copy, no cache)

     b. status=uploading
        gcs_files row for this clip?  yes & sha256 matches → reuse URI
                                       no                  → upload local file
                                                             to gs://<bucket>/clips/{id}.mov,
                                                             insert gcs_files row

     c. status=prompting
        gemini_client.annotate(gs_uri, mime, template.prompt,
                               template.output_schema, template.model)
        store raw + structured

     d. write annotations row
        (prompt_used, model, raw_response, structured_output,
         clip_snapshot, template_id, job_id)

     e. expand to review_items using template.target_map
        e.g. structured.scenes[]   → N review_items (kind=marker)
             structured.summary_cz → 1 review_item (kind=note)
             structured.decade     → 1 review_item (kind=field)

     f. status=review_ready
        SSE push to /api/jobs/{id}/events

   on any error: status=error, error_message set, continue with next item
```

### 4.2 Review and apply

The Review pane (HTMX-rendered, with the player widget described in §5) lets the user accept/edit/reject each `review_item`. When the user clicks **Apply accepted** for a clip:

1. `GET /clips/{id}` to fetch *current* CatDV state. Never trust `clip_snapshot` — the clip may have changed since annotation.
2. Build the PUT payload by **merging**:
   - **Markers:** current markers + accepted new markers (deduped on overlapping `in.frm`).
   - **Fields:** for each accepted field item, set `fields[identifier]` to `edited_value ?? proposed_value`; other fields untouched.
   - **Notes:** if a note item is accepted, write the value to the CatDV field named by the target_map entry's `target`, using `append` (default) or `replace` mode from the same target_map entry. Append mode joins existing + new with `\n\n---\n\n` separator.
3. `PUT /clips/{id}` with merged payload, wrapped in a `write_log` row.
4. On success: mark involved review_items `decision=accepted`, `applied_at=now`.
   On failure: keep `decision=pending`, record `write_log.status=error`, surface in UI.

PUT replaces the markers array wholesale — this merge step is the most safety-critical code in the app. It is covered by the heaviest unit-test load (§9).

### 4.3 Concurrency rules

- **One worker per job, items processed serially.** Parallel downloads over the VPN starve each other; one batch consumes the pipe predictably.
- **One job at a time, system-wide** (configurable). Avoids contention for both VPN and Vertex AI quota.
- **Cancellation:** job's `status=cancelled` is checked before each step; mid-step downloads/uploads are not killed (they finish or time out), but the next item won't start.
- **Crash recovery:** on app start, any `job_items` in transient states (`resolving`, `uploading`, `prompting`) are reset to `pending`; the job resumes.

### 4.4 Failure modes

| Failure | Detection | Recovery |
|---|---|---|
| VPN drops mid-download | httpx exception or timeout | Item → `error`, retryable; `Range` resume on retry |
| CatDV session expired | response envelope `status=AUTH` | `catdv_client` auto re-logs in once, retries transparently |
| CatDV write fails | response envelope `status=ERROR` | Item stays `review_ready`, `write_log` records error, surface in UI |
| GCS upload fails | google-cloud-storage exception | Item → `error`, retryable; partial uploads cleaned |
| Vertex returns SAFETY block | response field | Annotation row written with raw + null structured; item → `error` with reason |
| Vertex schema-parse failure | json/jsonschema error on response | Annotation row written with raw + null structured; item → `error`; raw inspectable in UI |
| Vertex 429 quota | HTTP status | Exponential backoff (up to N retries), then item → `error` |
| Apply payload merge bug | dry-run diff before PUT | Optional "confirm before apply" toggle in settings shows the diff |
| Disk full on proxy cache | OSError on write | LRU eviction over `PROXY_CACHE_CAP_GB`; alert if eviction can't keep up |

---

## 5. Review pane video integration

The review pane is the only screen with nontrivial client-side state. It's a single screen (no SPA routing) where the user verifies suggested annotations against the actual footage.

### 5.1 Layout

```
┌─ Reviewing: Abramcukova_Anna_09 (5/12 in job #42) ──────────────────┐
│                                                                     │
│  ┌────────────────────────┐   ┌─ Proposed changes ──────────────┐  │
│  │                        │   │                                 │  │
│  │   [ video player ]     │   │ ► marker  "Anna na zahradě"     │  │
│  │                        │   │   0:01:23:12–0:01:45:03         │  │
│  │  ────────────────────  │   │   [▶ play in–out]               │  │
│  │ ▶ ▮▮ │◀ ▶│             │   │   [set in←] [set out←]          │  │
│  │   00:01:23:12 /        │   │   ☑ accept    [edit]            │  │
│  │   0:08:42:00           │   │                                 │  │
│  │  ────────────────────  │   │   marker "rodinný portrét"      │  │
│  │   ◆──◆────◆──────◆──   │   │   0:02:10:18–0:02:30:24         │  │
│  │     markers timeline   │   │   ...                           │  │
│  └────────────────────────┘   │                                 │  │
│                               │   field decade = "30.léta"      │  │
│  [◀ prev]   [✓ apply]   [▶]   │   ▶ @0:00:04 ▶ @0:01:00          │  │
│                               └─────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

### 5.2 Player guarantees

- **Locally-served proxy.** Same `.mov` file already on disk (cached in dev, native in prod) streamed by FastAPI at `/api/media/{clipID}` with Range support. H.264-in-`.mov` plays natively in Safari and Chrome.
- **Frame-accurate seek.** `in/out` markers have `{frm, fmt, secs, txt}`. We seek by `secs` and display SMPTE from `currentTime × fps`. `fps` from `clip_snapshot.format/fps`.
- **Bidirectional linking** between marker list and player:
  - Click a review_item → player seeks to `in.secs`, item highlights.
  - `▶ play in–out` → seek, play, auto-pause at out.
  - Currently-playing range highlights its item in the list.
  - Mini-timeline diamonds clickable.
- **In-place marker edit.**
  - `[set in ← here]` / `[set out ← here]` write current `currentTime` (rounded to fps) into `review_items.edited_value`.
  - SMPTE numeric input also writes to `edited_value`.
  - Original `proposed_value` preserved alongside.
- **Keyboard shortcuts** (essential for batch review):
  | Key | Action |
  |---|---|
  | Space | play / pause |
  | J / K / L | shuttle (-1× / pause / +1×) |
  | `,` / `.` | step ±1 frame |
  | ↑ / ↓ | prev / next review item |
  | Enter | accept current item, advance |
  | Delete | reject current item, advance |
  | I / O | set in / out to current frame |
  | Cmd+Enter | apply all accepted for this clip, advance to next clip |

### 5.3 Non-marker items: optional timecode evidence

Field and note items don't carry timecodes natively. Templates can optionally request **timecode evidence** in the output schema:

```json
{
  "summary_cz": { "value": "...",     "evidence_secs": [12.4, 87.1, 142.0] },
  "decade":     { "value": "30.léta", "evidence_secs": [4.0, 60.0] }
}
```

When `evidence_secs` is present, the review pane renders little jump-buttons (`▶ @0:00:12 ▶ @0:01:27`) next to the field/note. No bloat when the template doesn't ask for evidence.

### 5.4 Implementation notes

- `backend/app/static/js/player.js` — vanilla JS class, ~300–400 lines: wraps `<video>`, exposes `seek(secs)`, `playRange(in, out)`, `setMarkers(list)`, dispatches `player:*` custom events. Alpine.js handles surrounding UI listening to those events.
- Mini-timeline is a `<canvas>` (markers as diamonds), redrawn on resize.
- Server stays authoritative: accept/reject/edit are HTMX form submissions; responses come back as HTML fragments.
- **Codec fallback (only if needed):** if Chrome stumbles on a specific `.mov` container, a one-time `ffmpeg` remux to `.mp4` (no re-encoding, seconds-fast) per clip, cached alongside. Not in v1 unless we hit playback issues; we will not ship `ffmpeg-static` by default.

---

## 6. Pluggable proxy resolver

The single dev/prod difference worth abstracting in code, because it touches every job item.

### 6.1 The abstraction

```python
class ProxyResolver(Protocol):
    def path_for(self, clip: ClipJson) -> Path: ...     # local file usable by GCS upload
    def is_managed(self, path: Path) -> bool: ...        # may we evict/delete?
```

Two implementations, selected at startup by `PROXY_SOURCE` env var:

- **`RestProxyResolver` (`PROXY_SOURCE=rest`, dev default)**
  - Downloads via `GET /catdv/api/9/clips/{id}/media` if not in `proxy_cache`; streams into `DATA_DIR/cache/proxies/{clipID}.mov`; returns that path.
  - `is_managed → True`. LRU eviction over `PROXY_CACHE_CAP_GB` (default 20 GB).
  - Updates `proxy_cache.last_used_at` on every read.

- **`FilesystemProxyResolver` (`PROXY_SOURCE=filesystem`, prod default)**
  - Resolves the clip's local path from `clip.media` / `clip.importSource` + `PROXY_FS_ROOT`. Exact resolution rule confirmed by inspecting a real clip JSON during dev (see Open Questions §11).
  - Returns the on-disk path **directly** — no copy, no cache, no download.
  - `is_managed → False`. Eviction is a no-op; we never touch CatDV's files.
  - `proxy_cache` table is bypassed; worker's status sequence becomes `pending → uploading → prompting → review_ready`.

### 6.2 Path resolution strategies (in order of preference)

1. **Direct from clip JSON:** if `clip.media` contains `proxyPath` or an absolute path when called by a server-local user, use as-is, or join with `PROXY_FS_ROOT`.
2. **Path template fallback:** `PROXY_PATH_TEMPLATE="{root}/{clipID}.mov"` rendered from clip metadata. Honza will confirm the convention for the Pragafilm install.

Final form is per-deployment config; the resolver accepts either.

### 6.3 Startup self-check

In `filesystem` mode, the app loads one or two clips, resolves their paths, `os.access(path, os.R_OK)`. If it fails (missing, unreadable), log a clear error and refuse to start. Cheap insurance against discovering misconfig mid-batch.

### 6.4 Linux permissions for prod

The deployed process must run under a Unix user that has read on the CatDV proxy directory (group membership on whatever group owns the CatDV data dir). This is an ops task for Honza when we deploy, not something the app handles — but the deploy doc calls it out as a precondition.

---

## 7. GCP infrastructure & GCS-direct video flow

### 7.1 The flow

Video bytes never pass through our backend to Vertex AI. We upload once to a GCS bucket and pass the `gs://` URI to Gemini, which reads directly from GCS using the service account's permissions.

```
proxy_resolver → local file → GCS upload (once) → gs:// URI → Vertex AI Gemini
                                                              (europe-west3)
```

### 7.2 Required GCP resources

A **separate GCP project** (the Archive-AI PoC project stays untouched).

| Resource | Purpose |
|---|---|
| GCP project | new project, e.g. `pragafilm-catdv-annotator` |
| Enabled APIs | `aiplatform.googleapis.com`, `storage.googleapis.com`, `secretmanager.googleapis.com`, `iamcredentials.googleapis.com` |
| GCS bucket | `<project>-catdv-proxies`, regional, **`europe-west3`** (same region as Vertex AI) |
| Service account | `catdv-annotator@<project>.iam.gserviceaccount.com` |
| Secret Manager | `CATDV_USERNAME`, `CATDV_PASSWORD` (prod only; dev uses `.env`) |

### 7.3 IAM (minimum-viable, narrowly scoped)

| Role | Scope | Why |
|---|---|---|
| `roles/storage.objectAdmin` | the bucket only | upload + delete clip proxies in our bucket |
| `roles/aiplatform.user` | project | call Vertex AI Gemini models |
| `roles/secretmanager.secretAccessor` | individual secrets | read CatDV creds in prod |

We deliberately **do not** grant `roles/storage.admin` project-wide (PoC did; we scope tighter). No `serviceAccountTokenCreator` — we don't use signed URLs.

### 7.4 Auth

- **Dev (Mac):** service account JSON at `~/.gcp/catdv-annotator-key.json`, `GOOGLE_APPLICATION_CREDENTIALS` set in `.env`. Same code path as prod.
- **Prod (CatDV server):** same — SA JSON at `/etc/catdv-annotator/sa.json`, mode 600, owned by the service user. Internet access available from the CatDV server for Vertex AI / GCS.

### 7.5 Secrets

`secrets.py` returns CatDV creds from either `.env` (when `APP_ENV=dev`) or Secret Manager (when `APP_ENV=prod`). Cached in memory after first fetch (mirrors PoC pattern).

### 7.6 Config (full list)

```ini
# App
APP_ENV=dev                                    # dev | prod
BIND_HOST=127.0.0.1
BIND_PORT=8765
DATA_DIR=./data

# CatDV
CATDV_BASE_URL=http://192.168.1.41:8080
CATDV_USERNAME=klientAI                        # dev only; prod from Secret Manager
CATDV_PASSWORD=...                             # dev only; prod from Secret Manager
CATDV_CATALOG_ID=881507

# Proxy resolution
PROXY_SOURCE=rest                              # rest (dev) | filesystem (prod)
PROXY_FS_ROOT=                                 # required when PROXY_SOURCE=filesystem
PROXY_PATH_TEMPLATE=                           # optional override
PROXY_CACHE_CAP_GB=20                          # rest mode only

# GCP / Vertex AI
GCP_PROJECT_ID=pragafilm-catdv-annotator
GCP_LOCATION=europe-west3
GCS_BUCKET_NAME=pragafilm-catdv-annotator-proxies
GOOGLE_APPLICATION_CREDENTIALS=/etc/catdv-annotator/sa.json
GEMINI_MODEL=gemini-2.5-pro                    # default; per-template override allowed
```

### 7.7 Setup script

`scripts/setup-gcp.sh` (idempotent), takes `$PROJECT_ID`:

```bash
gcloud services enable aiplatform.googleapis.com storage.googleapis.com \
  secretmanager.googleapis.com iamcredentials.googleapis.com \
  --project=$PROJECT_ID

gsutil mb -p $PROJECT_ID -l europe-west3 gs://$BUCKET_NAME

gcloud iam service-accounts create catdv-annotator --project=$PROJECT_ID

gsutil iam ch serviceAccount:$SA:objectAdmin gs://$BUCKET_NAME
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA" --role="roles/aiplatform.user"

gcloud secrets create CATDV_USERNAME --replication-policy=automatic
gcloud secrets create CATDV_PASSWORD --replication-policy=automatic
gcloud secrets add-iam-policy-binding CATDV_USERNAME \
  --member="serviceAccount:$SA" --role="roles/secretmanager.secretAccessor"
gcloud secrets add-iam-policy-binding CATDV_PASSWORD \
  --member="serviceAccount:$SA" --role="roles/secretmanager.secretAccessor"

gcloud iam service-accounts keys create catdv-annotator-key.json \
  --iam-account=$SA --project=$PROJECT_ID
```

No CORS, no Cloud Run, no Artifact Registry, no GitHub Actions — we deploy to the CatDV server.

### 7.8 Service code shape

```python
# services/gcs.py
from google.cloud import storage

class GcsService:
    def __init__(self, bucket_name: str):
        self._client = storage.Client()       # uses ADC
        self._bucket = self._client.bucket(bucket_name)

    def upload_if_absent(self, clip_id: int, local_path: Path, mime: str) -> str:
        blob_name = f"clips/{clip_id}.mov"
        blob = self._bucket.blob(blob_name)
        if not blob.exists():
            blob.upload_from_filename(str(local_path), content_type=mime)
        return f"gs://{self._bucket.name}/{blob_name}"

    def delete(self, clip_id: int) -> None:
        self._bucket.blob(f"clips/{clip_id}.mov").delete()
```

```python
# services/gemini.py
from google import genai

class GeminiService:
    def __init__(self, project: str, location: str):
        self._client = genai.Client(vertexai=True, project=project, location=location)

    def annotate(self, gcs_uri: str, mime: str, prompt: str,
                 schema: dict, model: str) -> dict:
        response = self._client.models.generate_content(
            model=model,
            contents=[
                {"text": prompt},
                {"file_data": {"file_uri": gcs_uri, "mime_type": mime}},
            ],
            config={
                "response_mime_type": "application/json",
                "response_schema": schema,
            },
        )
        return {"raw": response.model_dump(), "text": response.text}
```

Error classification (matching PoC):
- `quota` in error → 429, retry with backoff.
- `SAFETY` / `content policy` → record and surface, do not retry.
- `permission` / `access` → record and surface, operator must fix IAM.

### 7.9 Storage cost note

~10k clips × 200 MB ≈ 2 TB in `europe-west3` standard ≈ **~$45/month**. A GCS lifecycle rule (move to Nearline after 90 days of no access) is a cheap optimization we can add later. Not in v1.

---

## 8. Tech stack

### 8.1 Backend (Python)

| | Choice | Notes |
|---|---|---|
| Framework | **FastAPI** | async, typed, SSE-friendly |
| ASGI server | **Uvicorn** | |
| HTTP client | **httpx** | async, Range/streaming |
| Gemini SDK | **`google-genai`** | Vertex backend |
| GCS SDK | **`google-cloud-storage`** | |
| Secret Manager | **`google-cloud-secret-manager`** | |
| DB | **stdlib `sqlite3` + `aiosqlite`**, hand-written SQL | no ORM |
| Validation | **Pydantic v2** | one definition for HTTP, internal models, Gemini structured output |
| Background work | **`asyncio.create_task` in-process** | single worker, single user |
| SSE | **`sse-starlette`** | |
| Logging | stdlib `logging` + **`python-json-logger`** | structured stdout |
| Linting | **Ruff** | |
| Package mgmt | **`uv`** | with `.venv/bin/python` for execution per global rules |
| Test | **pytest + pytest-asyncio + httpx test client** | |

### 8.2 Frontend (Python + minimal JS)

One language top to bottom. No Node, no npm, no TypeScript, no build step beyond Tailwind CLI.

| | Choice | Notes |
|---|---|---|
| Templating | **Jinja2** | server-rendered HTML |
| Interactivity | **HTMX** | partial swaps, SSE, form submissions |
| Local JS state | **Alpine.js** | video player UI, modals |
| Styling | **Tailwind CSS standalone CLI** | single binary, no npm |
| Icons | **Lucide SVGs** as Jinja macros | |
| Video player | **vanilla `<video>` + custom controls** in `player.js` | ~300–400 lines |
| Forms validation | Pydantic server-side, HTMX returns error fragments | |

### 8.3 Packaging & ops

| | Choice |
|---|---|
| Layout | `backend/` (Python pkg) + `frontend/` (Jinja + static) + `tests/` + `deploy/` + `scripts/` + `docs/` |
| Dev start | `./run.sh` — boots venv, runs `uvicorn --reload` and Tailwind `--watch`, opens browser |
| Prod runtime | systemd unit running `uvicorn` from `.venv` on the CatDV server |
| Containerization | Optional Dockerfile (multi-stage) — not required |
| Config | `pydantic-settings` reading `.env` + env vars |
| Migrations | hand-written SQL files in `backend/migrations/NNNN_*.sql`, applied at startup |

### 8.4 Deliberate non-choices

- **No Electron/Tauri** — confirmed.
- **No React/TypeScript** — overkill for forms + one video screen.
- **No ORM** — schema is small, SQL is clear, FTS5 + JSON columns are easier without ORM ceremony.
- **No Redis/Celery** — one async worker in-process is right-sized.
- **No GraphQL** — REST + SSE is plenty.
- **No auth library** — single user, local bind.
- **No state machine library** — job/item statuses are a small enum.
- **No Cloud Run, no Artifact Registry, no GitHub Actions in v1** — `rsync + systemctl restart` is enough.
- **No `ffmpeg-static`** unless we hit Chrome `.mov` playback issues.

---

## 9. Testing strategy & v1 definition of done

TDD by default per global rules.

### 9.1 Test layers

1. **Pure logic — unit tests (no I/O).**
   - `target_map.expand(structured_output, target_map)` — every kind, missing keys, empty arrays.
   - `payload_builder.merge(current_clip, accepted_items)` — markers dedupe on overlapping `in.frm`, fields set, notes append/replace, other fields untouched. Heaviest coverage — this is the most safety-critical code in the app.
   - `timecode.fps_to_smpte(secs, fps)` and inverse — fractional rates (23.976, 29.97).
   - `output_schema.validate(response)`.

2. **Client wrappers — integration against fakes.**
   - `catdv_client` against a tiny FastAPI fake that mimics `{status, errorMessage, data}` — AUTH re-login, ERROR surfacing, Range-resumable downloads, timeouts.
   - `gemini_client` against a stub returning canned structured responses — Files-style URI handling, retry on 429, schema-parse failures.
   - `gcs_client` against the [GCS storage emulator](https://cloud.google.com/sdk/gcloud/reference/beta/emulators/storage) — idempotent upload, URI shape, delete.

3. **Worker loop — integration with all fakes wired.**
   - Run a fake 3-clip job end to end: resolve → upload → prompt → annotations → review_items → SSE events.
   - Crash recovery: kill worker mid-item, restart, transient statuses reset, job resumes.
   - Cancellation: `status=cancelled` mid-job, worker stops before next item, no orphans.

4. **Proxy resolver — both implementations.**
   - `RestProxyResolver` against fake CatDV: cache miss/hit, LRU eviction.
   - `FilesystemProxyResolver` against `tmp_path`: path resolution, fail-fast on missing/unreadable.
   - Same test suite parameterized over both — contract must be identical from worker's POV.

5. **Manual smoke checklist for UI.**
   - Annotate 2–3 short clips end to end, verify in CatDV web client.
   - Player SMPTE matches CatDV frame-for-frame.
   - Reject all → CatDV unchanged. Accept all → CatDV updated as proposed.
   - VPN drop mid-job → clear error, reconnect, retry succeeds.

**No frontend component tests in v1.** Manual smoke is enough for a single-user app. Playwright/Vitest can come later if the UI grows.

**Test isolation is enforced from day one.** Tests must pass alone *and* in the full suite. No "Task 19 fix later" backlog — isolation breakage blocks the PR that introduced it.

### 9.2 v1 Definition of Done

**Must work**

- Two-pane server-rendered UI (Jinja2 + HTMX, single-page feel via partial swaps) at `localhost:8765`:
  - CatDV pane: clip list, search by name + by `pragafilm.*` fields, clip detail view.
  - Annotation pane: templates CRUD, job submission, live job progress, review board.
- Login to CatDV with credentials from config; session persists; transparent re-login on `AUTH`.
- Template CRUD: name, prompt, JSON output schema, target_map, model.
- One seeded template ships: **"Scene markers + Czech summary + era classification"** writing markers + `pragafilm.popis.materialu` + `pragafilm.dekáda.natočení` + `pragafilm.rok.natočení`.
- Batch annotation job on N selected clips → live progress via SSE → survives browser refresh.
- Review pane with frame-accurate player, keyboard shortcuts, in-place marker editing.
- Apply accepted items per clip with safe merge into CatDV — markers never destroyed, fields never blanked.
- Crash recovery on restart.
- `write_log` audit trail of every PUT.
- Both `PROXY_SOURCE=rest` and `PROXY_SOURCE=filesystem` modes work and pass startup self-check.

**Must be present but quiet**

- Archive tables populated (`annotations`, `review_items`, `write_log`).
- FTS5 index on annotations, populated on insert. No search UI yet.
- `embeddings`, `tags` tables exist, unused.

**Out of scope (deferred)**

- The future AI-curation/search app (separate project).
- Embedding generation, semantic search, AI tagging.
- Catalog browsing UI (single `CATDV_CATALOG_ID` from config).
- General-purpose marker editor outside review.
- Multi-user, auth, sharing.
- Field-definition creation (CatDV admin task).
- Desktop bundling.
- Mobile/tablet UI.
- Cloud Run / Docker / CI deploy pipeline.

### 9.3 Operability requirements

- One-command dev start: `./run.sh`.
- Single `.env.example` documenting all config knobs.
- `deploy/` directory: systemd unit + `DEPLOY.md` covering Linux user, proxy directory read perms, env vars, gcloud setup.
- Logs to stdout as structured JSON (journald in prod) with `job_id` / `clip_id` correlation IDs.
- Startup self-check that fails loud and clear if CatDV unreachable, GCS bucket missing, SA key unreadable, or proxy resolver misconfigured.

---

## 10. Lessons from the Archive-AI PoC

Patterns kept; mistakes called out so we don't repeat them. Full table; this is the contract for what we *don't* do.

### Patterns worth keeping

| Pattern | Use here |
|---|---|
| `europe-west3` for Vertex + GCS (closest to Prague) | Same. |
| GCS-direct `gs://` URI to Vertex AI | Already integrated (§7). |
| Service account via `GOOGLE_APPLICATION_CREDENTIALS` | Same. |
| Repository pattern: storage primitives separate from business logic | `GcsService` / `CatdvClient` / `GeminiService` are primitives; `Annotator` + `JobWorker` are business logic. |
| Secrets cached in memory after first fetch | Same. |
| Vertex error classification (quota → 429, SAFETY → reject, permission → operator) | Same. |
| Region: `europe-west3` (Frankfurt) | Same. |
| Streaming responses | We use SSE for job progress (not Gemini text streaming in v1; we want full structured outputs). |
| Startup-time secret/config validation | Same; expanded into a structured startup self-check. |
| Structured logging from day one | Same. |
| "No production code without a failing test first" | Adopted from PoC handovers as repo policy. |

### Mistakes to avoid repeating

| What happened in PoC | Why it hurt | What we do |
|---|---|---|
| Drive integration kept as `drive.service.ts` "legacy (unused)" | Dead code pollutes; future readers re-trace abandoned approaches | Decide storage strategy day one; if we abandon an approach mid-build, the PR that abandons it deletes the code. No "legacy" labels. |
| OAuth + Passport + JWT + whitelist for a research tool | Months of test plumbing for ~0 real users | No app auth in v1. `127.0.0.1` bind. Add a thin auth layer only when needed. |
| `VideoRepository` reaches into `storage.storage` / `storage.bucketName` (flagged in PoC's CLAUDE.md as "known abstraction leak, accepted for now") | Once an abstraction is bypassed once, future refactors break callers silently | If a method is missing on a service, add it to the service — never reach past the boundary. Lint rule if it happens twice. |
| Each route file instantiates services on import — "stateless wrappers, so correctness is unaffected" | Hides lifecycle, makes testing harder, blocks shared state | One `AppContext` dataclass at startup; FastAPI `Depends` provides services. Singletons per process. |
| Test isolation issue (`8/24 fail when run together`) carried across 3 handover sessions as "Task 19, fix later" | "Fix later" never happens; flaky tests erode trust | Each test must pass alone *and* in the full suite. Isolation breakage blocks the PR that introduced it. |
| TipTap rich-text editor for prompts | Rich text inserts invisible HTML that confuses models and complicates versioning | Plain `<textarea>` for prompts. Versioning via DB. |
| Reached "95% complete" but never made prod ("vhodný pro interní testování, nikoliv pro produkční užití") | Classic 80/20 trap; every feature half-done, none fully done | v1 has a narrower feature set. First deployable milestone is one full annotate→review→apply cycle on the CatDV server. Everything else waits. |
| Multiple model dropdowns (2.5/3.0 × Pro/Flash/Lite) in UI | Choice paralysis; users don't know which to pick | One default in env (`gemini-2.5-pro`). Per-template override in JSON config. No runtime UI knob in v1. |
| `OAUTH_ENABLED=false` feature flag for local dev | Dev-only code paths rot; prod behaves differently than dev | No auth, no flag. If we add auth later, dev and prod share the same code with the same auth (different identities only). |
| Dual stack (Node frontend + Node backend, Jest + Vitest + ts-jest, two package.json) | The stack drift alone is a part-time job | Python only, one `pyproject.toml`, one test runner (`pytest`). Tailwind CLI binary is the only non-Python tool. |
| CI/CD to Cloud Run + Artifact Registry + GitHub Actions SA + key rotation for a PoC | Months of yak-shaving | Deploy is `rsync + systemctl restart`. Add CI when there's a second contributor. |
| `ffmpeg-static` baked into Docker for thumbnails | Adds binary dep for a feature CatDV already provides | Use CatDV's `/api/9/thumbnails/{thumbID}`. Only consider local ffmpeg as a one-time `.mov`→`.mp4` remux if Chrome playback fails. |
| `decisions.md` left as empty header despite real decisions made | Future contributors can't reconstruct *why* | Each significant decision logged as one paragraph (context, alternatives, choice, why) in `docs/decisions.md` as we make it. |
| Handover docs ballooned (41 KB `HANDOVER.md` + multiple dated handovers) carrying open tasks across sessions | Hard to find current state; running notes hide what's true now | Per-task notes in the implementation plan. One short post-merge summary per task. No running session log. |

---

## 11. Open questions / followups for implementation

- **Proxy filesystem path resolution** — exact form (`clip.media.proxyPath` direct? template-rendered?) requires inspecting one clip JSON during dev and confirming with Honza. The `ProxyResolver` interface accepts either; per-deployment config picks.
- **Vertex AI quota** — first batch run will reveal real-world rate limits. Worker has backoff but caps are per-project; may need a quota increase if running large batches.
- **Czech tokenization in FTS5** — `unicode61 remove_diacritics 2` handles diacritics; doesn't do lemmatization. If search quality is poor for the future search app, consider an external indexer (Tantivy, Meilisearch). Not a v1 concern.
- **Marker dedupe rule** — current spec says "overlapping `in.frm`"; may need a small tolerance (±1 frame) to avoid duplicates from re-annotation jitter. Decide based on first real run.
- **Re-annotation policy** — running the same template on the same clip twice creates two `annotations` rows (intentional, for history). Do review_items from the older annotation get archived/hidden? Spec says: yes, only the latest annotation's review_items are surfaced by default; older annotations are reachable via the archive view.

---

## 12. Glossary

| Term | Meaning |
|---|---|
| **clip** | A CatDV media entity (one media asset + metadata + markers). |
| **proxy** | The H.264-in-`.mov` preview rendition CatDV exposes via REST. Originals (ProRes 422 HQ) are not REST-accessible. |
| **marker** | A timecoded annotation on a clip (`{in, out, name, description, category, color}`). |
| **template** | A named annotation recipe: prompt + output JSON schema + target_map + model. |
| **target_map** | JSON mapping from schema fields to CatDV write targets (marker / note / field). |
| **review_item** | One proposed change derived from a Gemini annotation, pending user accept/edit/reject. |
| **annotation** | One permanent record of a Gemini run on a clip with a template. |
| **dev / prod** | Dev = on the user's Mac over VPN to CatDV. Prod = running on the CatDV server itself. |

---

## 13. Document control

- This is a design spec, not an implementation plan. The implementation plan (task breakdown) follows in a separate document.
- Significant design changes after first commit should be logged in `docs/decisions.md`, not rewritten silently here.
