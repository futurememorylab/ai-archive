# AI Archive

Local-first web app for media archives: AI annotation jobs against
Gemini (Vertex AI) with results written back to the archive provider.
CatDV and filesystem-sidecar archives are supported today; the provider
interface (`ArchiveProvider`) is the extension point for others.

## Quick start (dev)

```bash
git clone <repo>
cd ai-archive
cp .env.example .env
# Edit .env — see .env.example for required variables per provider
./run.sh
```

Then:

```bash
curl -s http://localhost:8765/api/health
```

Open the UI at `http://localhost:8765/` (clips list) — first user-facing surface.

- **Cache view:** `http://localhost:8765/cache` — manage local proxy cache (status, queue, evict).

## Running on the CatDV host (no proxy cache)

When the app runs on the **same machine as the CatDV server**, it can read
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

## Running offline (no archive provider)

When the archive provider is unavailable (e.g. CatDV VPN down), run with:

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

- The browser holds no Google credentials. The clip chat sends each
  question to the app, and the server asks Gemini on Vertex with the same
  service account as annotation (ADR 0130). There is no Gemini API key.
- CatDV credentials (`CATDV_PASSWORD`) live in `.env` and are read
  server-side only; they are NOT exposed to the browser.

The rest of the threat model (CatDV seat limits, VPN dependency) still
applies.

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
- `backend/app/archive/` — provider ports + adapters (CatDV, filesystem)
- `backend/migrations/` — SQL migrations (applied at startup)
- `backend/seeds/` — default templates
- `tests/` — unit + integration tests
- `docs/specs/` — design specs
- `docs/plans/` — plans for in-flight work (deleted once merged)
- `docs/DEPLOY.md` — local/on-prem deployment + one-time GCP bootstrap
- `deploy/` — Cloud Run deployment (`deploy/README.md`)

## Annotating a clip

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

## Chat with AI about a clip

On a clip whose video is ready, ⋯ → **Chat with AI** opens a text chat
under the video. Gemini greets in Czech and describes the frame on screen;
each question you type goes with the current frame, and **Send frame** asks
about the frame you are on. Answers come back as text after a few seconds.
See `docs/specs/2026-10-18-clip-chat-on-vertex-design.md`.

### Setup

None beyond the normal Vertex service account (the one-time GCP bootstrap
in `docs/DEPLOY.md`). The chat runs on Vertex like annotation and shares
its bill. The chat prompt (`chat.system_instruction.cs`) is seeded on
startup and can be edited on the Prompts page.

### Env vars

- `GEMINI_CHAT_MODEL` — default `gemini-3.8-flash`. It needs a
  `GCP_LOCATION` that serves the model; 3.8 Flash is not in
  `europe-west3`, so use `GCP_LOCATION=global`.

### What's stored

Chats land in the `live_sessions` SQLite table (it keeps its Live-era
name), saved turn by turn, and show in the **History** tab next to
*Markers / Fields / Notes* on the clip page. The History panel is
read-only: transcripts and Czech summaries are never pushed into draft
annotations. Each turn's tokens and cost are recorded with the other
Gemini runs.

## License

Released under the [MIT License](LICENSE) © 2026 Future Memory Lab.

This repository also bundles a few third-party assets (Alpine.js, htmx, and
the Inter and JetBrains Mono fonts) under their own licenses — see
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md). "CatDV" is a trademark
of Square Box Systems; it is referenced here only for interoperability and
this project is not affiliated with or endorsed by them.
