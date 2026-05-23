# ARCHITECTURE — layer map

One picture, one table, one pointer block. If you need a noun defined,
read `docs/CONTEXT.md` first.

## Layers

```
              ┌────────────────┐
              │   routes/      │  ← HTTP + Jinja templates
              └───────┬────────┘
                      │
              ┌───────▼────────┐
              │   services/    │  ← orchestration, queues, monitors
              └───────┬────────┘
                      │
        ┌─────────────┼─────────────┐
        │             │             │
┌───────▼──────┐ ┌────▼─────┐ ┌─────▼──────┐
│ repositories │ │ archive/ │ │ models/    │  ← Pydantic / dataclass
└──────────────┘ └──────────┘ └────────────┘
        │             │
   ┌────▼─────┐  ┌────▼─────┐
   │ aiosqlite│  │ httpx /  │
   │ (app.db) │  │ FS / GCS │
   └──────────┘  └──────────┘
```

- `routes/` — FastAPI routers; render Jinja templates and HTMX
  partials. No SQL, no `httpx` calls; goes through services.
- `services/` — orchestration: queues, monitors, prefetchers, the
  cache inspector/actions, the workspace lifecycle. Holds *no*
  per-request state; everything is wired in `backend/app/context.py`.
- `repositories/` — raw-SQL over `aiosqlite`. One module per table
  (or close cluster); no orchestration logic, no provider calls.
- `archive/` — ports + adapters for the two external surfaces
  (`ArchiveProvider`, `AIInputStore`) and the canonical domain
  dataclasses (`CanonicalClip`, `ChangeSet`, `FieldDef`, …). The
  rest of the app talks to these protocols, not to httpx / GCS.
- `models/` — Pydantic models for the *app's* own state (Prompt,
  Annotation, LiveSession). Archive-side dataclasses live in
  `archive/model.py`, not here.

### Layer rules are enforced by import-linter

`import-linter` runs on every commit (see `.importlinter` for the
contracts and `.pre-commit-config.yaml` for the hook). The contract is
deliberately the *looser* option from plan §2.1: routes may call either
services or repositories directly (current practice in
`routes/jobs.py` and `routes/live.py`), but they must not reach into
archive adapter internals (`archive.providers`, `archive.registry`,
`archive.ai_stores`, etc.) — only the pure type modules
`archive.errors` and `archive.model` are fair game. Services must not
import routes, and models must not import services, repositories, or
routes. Run `.venv/bin/lint-imports` to check locally.

## Symptom → first file to read

| Symptom | First file to read |
|---|---|
| Marker save 502 | `routes/catdv.py`, `services/write_queue.py`, `services/sync_engine.py` |
| Proxy 404 / "unavailable" | `services/proxy_resolver.py`, `repositories/proxy_cache.py` |
| Live session never starts | `routes/live.py`, browser-direct WSS (no backend bridge) |
| Sync stuck "in_flight" | `repositories/pending_operations.py` + crash-recovery in `context.build()` |
| Connection pill stays red | `services/connection_monitor.py`, `routes/connection.py` |
| Workspace prep stalls on a clip | `services/workspace_manager.py`, `services/proxy_resolver.py` |
| Cache view shows stale rows | `services/proxy_cache_reconciler.py` (runs at startup), `services/cache_inspector.py` |
| LRU evicted a pinned clip | It shouldn't. See `services/lru_eviction.py` + `repositories/workspaces.py::pinned_clip_keys` |
| Gemini upload keeps repeating | `archive/ai_stores/gcs/adapter.py`, `repositories/ai_store_files.py` |
| Prompt edit rejected as "immutable" | `repositories/prompts.py::VersionImmutableError` |

## Where else to look

- `docs/CONTEXT.md` — domain glossary; one sentence per noun.
- `docs/adr/` — architecture decisions, one MADR-lite file per decision
  (`NNNN-slug.md`). `docs/decisions.md` is now just the index.
- `docs/specs/` — feature designs (one per dated spec).
- `docs/plans/` — implementation plans, one per PR / feature slice.
- `docs/DEPLOY.md` — production deployment guide.
- `docs/fs-archive-format.md` — sidecar layout for the FS provider.
- `docs/gemini-live-lessons.md` — what we learned wiring Live.
