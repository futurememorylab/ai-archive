# CatDV Annotator — Backend

Local-first web app for the Pragafilm CatDV archive: AI annotation jobs against
Gemini (Vertex AI) with results written back to CatDV.

**Backend only at this point.** UI is Plan B.

## Quick start (dev)

```bash
git clone <repo>
cd catdv-annotator
cp .env.example .env
# Edit .env — at minimum: CATDV_PASSWORD and GOOGLE_APPLICATION_CREDENTIALS
./run.sh
```

Then:

```bash
curl -s http://localhost:8765/api/health
```

Open the UI at `http://localhost:8765/` (clips list) — first user-facing surface.

- **Cache view:** `http://localhost:8765/cache` — manage local proxy cache (status, queue, evict).

## Running on the CatDV host (no proxy cache)

When the annotator runs on the **same machine as the CatDV server**, it can read
each clip's web proxy directly from CatDV's media-store directory instead of
downloading it over HTTP. No `data/cache/proxies/` is written, no
`proxy_cache` rows are recorded, and Gemini still receives the small H.264
web proxy.

In `.env`:

```
PROXY_SOURCE=filesystem
```

That's the only change. At startup the app fetches the hires→proxy mapping
from `GET /catdv/api/9/mediastores` and resolves each clip's proxy by
swapping the hires-root prefix in `media.filePath` for the matching
proxy root (for this installation that's `/Volumes/ARECA/CatDV_Proxy/`
and `/Volumes/ARECA2/CatDV_Proxy/`).

Requirements:

- The OS user running the app must have **read access** to every
  directory listed under `mediaType: proxy, target: web` in
  `/catdv/api/9/mediastores`.
- The CatDV media-store volumes must be mounted on the host. If they
  aren't, every clip's resolver call raises `ProxyNotFound: ... proxy
  not on disk` — there is no automatic fallback to the REST resolver
  (failing loudly is intentional).

UI affordances tied to the local proxy cache (Cache filter dropdown,
"Cache locally" / "Remove from local cache" actions, per-clip Evict
buttons) are hidden automatically in this mode.

See `docs/DEPLOY.md` → "Running on the CatDV host (no proxy cache)"
for failure-mode details.

## Running offline (no CatDV at all)

When the CatDV VPN is unavailable, run with:

```
CATDV_OFFLINE=true
```

The app will:

- Boot without attempting CatDV login (no seat taken).
- Serve the clip list and clip details from the local SQLite cache.
- Serve proxies only when already cached to `data/cache/proxies/`.
- Hide the Annotate, "Cache locally", and "Refresh from CatDV" actions.
- Show a red "Offline (forced)" chip in the header.

Writes (annotations, marker edits) made while offline are queued by the
existing `WriteQueue` and flushed when the app is next started without
the flag. To go back online, unset the flag and restart.

### Auto-fallback (no env flag)

When `CATDV_OFFLINE` is not set, the app boots normally but **degrades to
offline automatically** if the initial CatDV login fails or if a periodic
health probe fails mid-session. The header chip turns yellow and shows
"Offline — click to reconnect"; clicking it triggers a single probe via
`POST /api/connection/retry`. The probe loop only resumes once the user
successfully reconnects.

## Security caveats

This is a local app with a deliberately narrow threat model: **single
operator, on the operator's own laptop, behind the project VPN**.

- `GEMINI_API_KEY`, when configured, is **shipped to the browser** by
  the Live-session flow. Real ephemeral-token auth was attempted
  (`authTokens.create`) but Google closes the WSS handshake with code
  1007 "API key not valid" the moment the client sends `setup` — see
  ADR 0043. Until that's resolved upstream, treat the key as
  browser-readable. Do not deploy this app on a shared host, behind a
  public network, or under any model where browser dev-tools access by
  an untrusted user is a concern.
- The boot log emits a `WARNING` naming this exposure every time the
  key is configured, so the operator sees it on every start.
- CatDV credentials (`CATDV_PASSWORD`) live in `.env` and are read
  server-side only; they are NOT exposed to the browser.

If you need to relax these constraints, the live-session auth flow has
to be redesigned. That is a separate project, not a config change.

## Architecture & orientation

New to the codebase? Read these two first:

- [`docs/CONTEXT.md`](docs/CONTEXT.md) — domain glossary; one sentence per
  noun (Clip, Workspace, Write Queue, Live Session, …).
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — layer map plus a
  "symptom → first file to read" table for triage.

## Tests

```bash
.venv/bin/pytest -q
```

## Layout

- `backend/app/` — FastAPI app, services, repositories, routes
- `backend/migrations/` — SQL migrations (applied at startup)
- `backend/seeds/` — default templates
- `tests/` — unit + integration tests
- `docs/specs/` — design spec
- `docs/plans/` — implementation plans
- `docs/DEPLOY.md` — local/on-prem deployment + one-time GCP bootstrap
- `deploy/` — Cloud Run deployment (`deploy/README.md`), Gemini Live key script

## Status

- Backend plan: see `docs/plans/2026-05-18-catdv-annotator-backend.md`
- UI MVP: `docs/plans/2026-05-20-ui-mvp.md` (clips list + clip detail, read-only)
- Media prefetch + cache UI: `docs/plans/2026-05-20-pr8-media-prefetch-and-cache-ui.md`
- Prompt management: `docs/plans/2026-05-21-prompt-management.md`

## Annotate a clip from the UI

Once the one-time GCP bootstrap (see `docs/DEPLOY.md` → "One-time GCP
bootstrap") has been run and `.env` has the GCP variables set:

1. Open a clip detail page (e.g. `http://localhost:8765/clips/881603`).
2. Click **Annotate** in the header — a dropdown lists every prompt that has a
   production version.
3. Pick one. The right aside switches to **Draft** and shows a status line:
   *Locating proxy → Uploading proxy to GCS → Calling Gemini → Done*.
4. When the run finishes, the Draft tabs render the proposed markers / fields /
   notes in the same visual treatment as the **Published** tabs (which show the
   current CatDV state). Toggle between them with the Published↔Draft segmented
   control above the tabs.
5. Each run persists an annotation row + review_items in the local DB. The
   Draft view always shows the **latest** annotation for the clip — re-running
   replaces what's visible.

Notes:

- The proxy is fetched and cached on demand if not already local. The status
  line tells you when this is happening.
- Accept / reject of proposed items and pushing them back to CatDV are out of
  scope in this iteration; both flows already have backend hooks
  (`review_items.decision`, `write_queue`) and will land in a follow-up.
- If no prompt has a production version, the dropdown links to `/prompts`.

## Gemini Live clip assistant (optional)

Voice-driven Czech assistant on the clip-detail page. Browser opens a WebSocket
directly to Google's Gemini Live API using ephemeral tokens minted by the
backend; audio bytes never traverse our process.

### Setup (one-shot)

There's exactly one operator-run script. Everything else (SQLite migration
`0010_live_sessions`, the Czech `live.system_instruction.cs` prompt seed,
and the stale-pending session reaper) is wired into the FastAPI `lifespan`
and runs automatically the next time you start the app.

Prereqs:

- `gcloud` CLI installed and `gcloud auth login` already done
- `gcloud components install alpha` (the API-keys subcommand lives there)
- The signed-in account has `roles/serviceusage.serviceUsageAdmin` and
  `roles/serviceusage.apiKeysAdmin` on `$GCP_PROJECT_ID`
- This is **separate** from the `GOOGLE_APPLICATION_CREDENTIALS` setup
  (the one-time GCP bootstrap in `docs/DEPLOY.md`). Vertex / batch
  annotation keeps using the service-account key; Live uses a Generative
  Language API key.

```bash
# 1. Mint the API key (idempotent — re-uses an existing one if present)
GCP_PROJECT_ID=<your-project> ./deploy/enable-gemini-live.sh

# 2. The script prints the key value. Paste it into .env:
echo 'GEMINI_API_KEY=<printed-key>' >> .env

# 3. Restart the app. Migration + seed apply on lifespan start.
./run.sh
```

Open any clip with `duration_secs > 0` while the app is in `online` mode —
a `🎤 Live` button appears in the header next to *Annotate ▾*.

If you skip step 1, the `GEMINI_API_KEY` is empty and the feature stays
silent: schema and seed still apply on startup, but `🎤 Live` requests
will get a `RuntimeError: GEMINI_API_KEY is not configured` from the
session-config endpoint, so the button is gated to `mode == "online"`
only and the operator never sees it without a key.

### Env vars

All four are optional. The Live button only appears when `GEMINI_API_KEY`
is set; the other three have working defaults.

- `GEMINI_API_KEY` — Generative Language API key (printed by the script
  above). When unset, the Live feature is disabled.
- `GEMINI_LIVE_MODEL` — default `gemini-2.5-flash-preview-native-audio-dialog`.
- `GEMINI_LIVE_VOICE` — default `Aoede` (speaks `cs-CZ` because
  `speechConfig.languageCode` pins it).
- `GEMINI_LIVE_INACTIVITY_S` — default `60`. Mutual-silence timeout
  before the session auto-closes with `end_reason=inactivity`.

`.env.example` ships with these four lines already, with the key blank;
just paste the value the script prints.

### What's stored

Sessions land in the `live_sessions` SQLite table and surface as a
**History** tab next to *Markers / Fields / Notes* on the clip page.
The History panel is read-only — transcripts and Czech summaries are
never auto-pushed into draft annotations. Audio bytes are not stored
at all (only the transcripts that Gemini emits).
