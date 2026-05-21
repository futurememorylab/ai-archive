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
