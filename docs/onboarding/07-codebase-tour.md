# 07 — Codebase tour and reading list

The tree is small enough to scan in a sitting; this page is the map
plus a triage table so you can dive straight to the right file when
something breaks.

## Top-level layout

```
ai-archive/
├── backend/
│   ├── app/               ← the FastAPI app (see below)
│   ├── migrations/        ← hand-written SQL, applied in order at startup
│   └── seeds/             ← default prompt(s) seeded on first boot
├── tests/                 ← unit + integration; mirrors backend/app/
├── docs/
│   ├── onboarding/        ← you are here
│   ├── adr/               ← architecture decisions (one MADR-lite file each)
│   ├── specs/             ← feature design specs
│   ├── plans/             ← per-PR implementation plans
│   ├── ARCHITECTURE.md    ← canonical layer map + symptom→file table
│   ├── CONTEXT.md         ← one-sentence glossary
│   ├── DEPLOY.md          ← production deployment
│   └── decisions.md       ← ADR index
├── deploy/                ← Cloud Run deploy (README, Dockerfile bits, env,
│                            entrypoint, Litestream)
├── run.sh                 ← venv + uvicorn launcher
├── pyproject.toml         ← deps, ruff, pytest, basedpyright, interrogate
├── .importlinter          ← layer contracts
├── .pre-commit-config.yaml
└── CLAUDE.md              ← repo-scoped agent guidance
```

## Inside `backend/app/`

```
backend/app/
├── main.py                ← FastAPI app, lifespan, register_routers
├── context.py             ← AppContext dataclass — the composition root
├── settings.py            ← pydantic-settings model (reads .env)
├── deps.py                ← FastAPI dependencies (get_ctx, etc.)
├── db.py                  ← aiosqlite open + pragmas
├── migrations_runner.py   ← apply_migrations() called from AppContext.build()
├── seed.py                ← seed_default_prompt, seed_system_instruction
├── startup.py             ← run_startup_cleanup (stale-pending reaper, etc.)
├── secrets.py             ← Secret Manager wrapper (prod)
├── logging_setup.py       ← python-json-logger config
├── timecode.py            ← SMPTE timecode helpers
│
├── archive/               ← the two external-surface PORTS + adapters
│   ├── provider.py        ← ArchiveProvider protocol
│   ├── ai_store.py        ← AIInputStore protocol
│   ├── model.py           ← CanonicalClip, ChangeSet, FieldDef, …
│   ├── errors.py
│   ├── registry.py        ← build_archive_provider(settings) → ArchiveProvider
│   ├── providers/
│   │   ├── catdv/         ← REST adapter (httpx)
│   │   └── fs/            ← FS sidecar adapter
│   └── ai_stores/
│       ├── registry.py
│       ├── gcs/           ← production AIInputStore
│       └── gemini_files/  ← stub
│
├── models/                ← Pydantic models for the app's OWN state
│                            (Prompt, Annotation, LiveSession) — NOT archive types
│
├── repositories/          ← raw SQL over aiosqlite — one module per table
│   ├── annotations.py
│   ├── ai_store_files.py
│   ├── cache_actions_log.py
│   ├── clip_cache.py
│   ├── clip_list_cache.py
│   ├── field_def_cache.py
│   ├── jobs.py
│   ├── live_sessions.py
│   ├── pending_operations.py    ← the write queue
│   ├── prefetch_queue.py
│   ├── prompts.py               ← versioned prompts + VersionImmutableError
│   ├── proxy_cache.py
│   ├── review_items.py
│   ├── workspaces.py
│   └── write_log.py
│
├── services/              ← orchestration; no per-request state
│   ├── annotator.py             ← end-to-end "annotate one clip" pipeline
│   ├── catdv_client.py          ← thin httpx wrapper used by the catdv adapter
│   ├── gcs.py                   ← AIInputStore glue
│   ├── gemini.py                ← Vertex AI call
│   ├── proxy_resolver.py        ← rest|filesystem proxy locator
│   ├── proxy_cache_reconciler.py← index ↔ disk reconciler (runs at startup)
│   ├── media_store_map.py       ← hires→proxy root mapping for filesystem mode
│   ├── workspace_manager.py
│   ├── write_queue.py           ← enqueue ChangeOps
│   ├── sync_engine.py           ← drains pending_operations to CatDV
│   ├── connection_monitor.py    ← online/degraded/offline/syncing state machine
│   ├── lru_eviction.py
│   ├── media_prefetcher.py
│   ├── cache_inspector.py       ← read API across all three cache layers
│   ├── cache_actions.py         ← write API (evict / refresh) + audit log
│   ├── clip_list_filters.py
│   ├── draft_view.py
│   ├── target_map.py            ← prompt output → marker/field/note routing
│   ├── live_sessions.py         ← clip chat: turns, transcript, summary
│   ├── live_context.py
│   └── events.py                ← in-process EventBus for SSE
│
├── routes/                ← FastAPI routers; no SQL, no httpx
│   ├── catdv.py
│   ├── cache.py                 ← exposes api_router, page_router, ui_router
│   ├── connection.py
│   ├── events.py                ← SSE
│   ├── jobs.py
│   ├── live.py                  ← clip chat: start, turns, end, history
│   ├── media.py
│   ├── prompts.py
│   ├── review.py
│   ├── sync.py
│   ├── ui.py
│   ├── workspaces.py
│   └── pages/                   ← server-rendered page routers
│
├── templates/             ← Jinja2 — page-level templates + HTMX partials
├── static/                ← Tailwind CSS, player.js, favicon
└── ui/                    ← small Python helpers used by templates
```

## Symptom → first file to read

Copied here for convenience from
[`../ARCHITECTURE.md`](../ARCHITECTURE.md), with extra commentary.

| Symptom | First file to read |
|---|---|
| Marker save returns 502 | `routes/catdv.py`, `services/write_queue.py`, `services/sync_engine.py` |
| Proxy 404 / "unavailable" | `services/proxy_resolver.py`, `repositories/proxy_cache.py` |
| Clip chat never answers | `routes/live.py`, `services/live_sessions.py`, `GeminiService.chat` (Vertex; check `GEMINI_CHAT_MODEL` / `GCP_LOCATION`) |
| Sync stuck in `in_flight` | `repositories/pending_operations.py` + the crash-recovery branch in `context.build()` |
| Connection pill stays red | `services/connection_monitor.py`, `routes/connection.py` |
| Workspace prep stalls on a clip | `services/workspace_manager.py`, `services/proxy_resolver.py` |
| `/cache` view shows stale rows | `services/proxy_cache_reconciler.py` (runs at startup), `services/cache_inspector.py` |
| LRU evicted a pinned clip | It shouldn't. See `services/lru_eviction.py` + `repositories/workspaces.py::pinned_clip_keys` |
| Gemini upload keeps repeating | `archive/ai_stores/gcs/adapter.py`, `repositories/ai_store_files.py` |
| Prompt edit rejected as "immutable" | `repositories/prompts.py::VersionImmutableError` |
| App takes a CatDV seat we didn't expect | `context.py::AppContext.build()` and the lifespan in `main.py` — and read [`05-catdv-license-discipline.md`](./05-catdv-license-discipline.md) |

## Suggested reading list for week one

In order, with target durations:

1. **(20 min) Setup**
   - [`04-running-locally.md`](./04-running-locally.md) — get the
     server up.
   - [`05-catdv-license-discipline.md`](./05-catdv-license-discipline.md)
     — internalise this before you've leaked a seat the hard way.

2. **(30 min) Mental model**
   - [`01-overview.md`](./01-overview.md) — product framing.
   - [`02-architecture.md`](./02-architecture.md) — layer map and key
     flows.
   - [`../CONTEXT.md`](../CONTEXT.md) — glossary; read top to bottom.

3. **(40 min) Standards + first task**
   - [`06-coding-standards.md`](./06-coding-standards.md) — pre-commit
     installed, baseline understood.
   - Run `.venv/bin/pytest -q`. Skim the test layout; pick one
     failing or skipped test as a starter task.

4. **(60 min, dipping in) Background reading**
   - [`../specs/2026-05-18-catdv-annotator-design.md`](../specs/2026-05-18-catdv-annotator-design.md)
     — the original design. Section 3 (data model) is the most
     load-bearing and slightly out of date — confirm against
     `backend/migrations/`.
   - The ADRs in the order they were written (`0001` → `0022`). Skim
     titles, read the ones whose context you'll touch.
   - [`../fs-archive-format.md`](../fs-archive-format.md) only if
     you'll work on the FS provider.
   - [`../gemini-live-lessons.md`](../gemini-live-lessons.md) only if
     voice comes back (Live was retired for the clip chat, ADR 0130).

## When you ship your first change

- Run `.venv/bin/pre-commit run --all-files`.
- Run `.venv/bin/pytest -q`.
- If the change involved a real decision (not just mechanical work),
  drop an ADR — see
  [`06-coding-standards.md`](./06-coding-standards.md#architecture-decisions-adrs).
- If the change is part of a multi-PR feature, drop a plan under
  `docs/plans/YYYY-MM-DD-<slug>.md` and delete it once the work merges.
- Restart with `kill -TERM <pid>` and wait for "Application shutdown
  complete" in the log. Then start the new build.
