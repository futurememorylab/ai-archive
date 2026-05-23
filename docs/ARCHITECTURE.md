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

### Layer rules are aspirational today

The diagram is what we *want*. In practice some routes still call
repositories directly (`routes/jobs.py` reaches `ctx.jobs_repo`,
`routes/live.py` instantiates `LiveSessionsRepo()`). This is a known
deviation. PR C of the architecture plan
(`docs/plans/2026-05-23-codebase-architecture-tier-2-and-beyond.md`
§2.1) will add `import-linter` to enforce a layering contract; until
then, treat the diagram as the intended direction of new code, not a
guarantee about every existing module.

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
- `docs/decisions.md` — historical decisions in append-only form;
  will migrate to `docs/adr/NNNN-*.md` per the architecture plan §3.4.
- `docs/specs/` — feature designs (one per dated spec).
- `docs/plans/` — implementation plans, one per PR / feature slice.
- `docs/DEPLOY.md` — production deployment guide.
- `docs/fs-archive-format.md` — sidecar layout for the FS provider.
- `docs/gemini-live-lessons.md` — what we learned wiring Live.
