# 0047. Split AppContext into CoreCtx + LiveCtx; unify route deps

**Date:** 2026-05-30
**Status:** Accepted

## Context

`AppContext` was a single ~30-field dataclass with ~15 `Foo | None`
service fields. The offline/online contract lived entirely at runtime:
`attach_provider` / `attach_ai_store` late-binding wired the cache
services after the archive subsystem, and every live call site guarded
with `assert ctx.foo is not None` or `getattr(ctx, "foo", None)`. Routes
all pulled the one ctx via `get_ctx` and `routes/cache.py` repeated a
`get_ctx` + `_inspector` + `_actions` triple.

This is tier-3 task T3-A1 + T3-A2 (combined — the shared route typing
cannot be left green in isolation).

## Alternatives

- **Inheritance (`LiveCtx(CoreCtx)`)** — duplicates fields and muddies
  which lifetime owns what. Rejected for composition.
- **Keep one ctx, drop the asserts only** — leaves the Optional soup and
  the late-binding; the type system still can't carry the contract.
- **`app.state.ctx` retained as an alias** — keeps 24 test files and the
  topbar templates unchanged, but re-introduces the ambiguous single
  object the split exists to remove.

## Decision

- `CoreCtx` (dataclass) carries everything **always present** even when
  `init_external=False`: settings, db, all repos, write_queue, event_bus,
  and the two DB-first cache services (`cache_inspector` / `cache_actions`).
  No Optional service fields.
- `LiveCtx` (dataclass) **composes** a `CoreCtx` (a `core` field, not
  inheritance) and adds the external services. `archive` / `ai_store` /
  `gemini` / `sync_engine` / `connection_monitor` / `workspace_manager` /
  `lru_eviction` / `_gcs_service` are non-Optional (always built when
  `init_external`). `catdv` / `proxy_resolver` / `thumbnail_service` /
  `media_prefetcher` stay legitimately Optional (offline-but-booted,
  fs-mode, cache-only resolver). `LiveCtx` exposes every CoreCtx field via
  a thin typed `@property` delegator, so a live handler reads `ctx.db` and
  `ctx.archive` off one object.
- `attach_provider` / `attach_ai_store` are **deleted**. `build_context`
  reorders construction: core → archive subsystem → wire cache services
  with the (possibly-None) provider/ai_store passed directly to their
  constructors → sync subsystem → assemble `LiveCtx`. The connection
  monitor's `is_online` closure reads the monitor through a small mutable
  holder, preserving the previous defer-read behaviour exactly.
- `build_context(settings, init_external)` returns `(CoreCtx, LiveCtx | None)`.
  The lifespan stashes both: `app.state.core_ctx` (always) and
  `app.state.live_ctx` (None offline). `app.state.ctx` is removed.
- `deps.py` exposes `get_core_ctx` (always) and `get_live_ctx` (raises a
  typed `503 "CatDV/Gemini offline"` when `live_ctx is None`). Each handler
  picks one by what it touches; `routes/cache.py`'s `_inspector` /
  `_actions` helpers are deleted (cache services are CoreCtx fields).

## Consequences

- The offline/online contract is now a type, surfaced at the route edge as
  a single 503 instead of scattered asserts. basedpyright sees through the
  delegators; it now flags 3 pre-existing `int | None` arg bugs in
  jobs/review that the old untyped soup hid (left for a separate fix), and
  one `write_queue`-None false positive disappeared.
- Tests that boot offline but exercise a live route inject via a new
  `tests/_helpers/live_ctx.py::install_live_ctx(app, **overrides)`, which
  wraps the booted CoreCtx in a LiveCtx (unspecified services default to
  MagicMock; the connection monitor defaults to an online stub so the
  topbar renders `mode == "online"`). This replaces the old
  `app.state.ctx.archive = ...` injection.
- Handlers that *degrade* offline (clip list/detail, studio partials,
  connection pill, ui switcher) read live services through
  `request.app.state.live_ctx` with a None guard rather than 503-ing.
  Templates (`layout.html`, `_connection_chip.html`, `_topbar_pills.html`)
  read `live_ctx` / `core_ctx` directly.
