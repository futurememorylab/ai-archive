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
- `docs/DEPLOY.md` — production deployment guide
- `scripts/setup-gcp.sh` — one-time GCP infra setup

## Status

- Backend plan: see `docs/plans/2026-05-18-catdv-annotator-backend.md`
- UI MVP: `docs/plans/2026-05-20-ui-mvp.md` (clips list + clip detail, read-only)
- Media prefetch + cache UI: `docs/plans/2026-05-20-pr8-media-prefetch-and-cache-ui.md`
- Prompt management: `docs/plans/2026-05-21-prompt-management.md`

## Annotate a clip from the UI

Once `scripts/setup-gcp.sh` has been run and `.env` has the GCP variables set:

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
