# 0021. Collapse duplicate cache_inspector/cache_actions construction (PR H of arch plan)

- **Date:** 2026-05-23
- **Status:** Accepted
- **Lifespan:** Invariant

## Context

`CacheInspector` (services/cache_inspector.py, ~440 LOC) and
`CacheActions` (services/cache_actions.py, ~530 LOC) were each
constructed **twice** during `AppContext.build`:

1. In `_build_cache_subsystem`, with `provider=None` and `ai_store=None`
   — pure-DB so they could be wired up before any external service
   existed (this is what the `init_external=False` test path uses).
2. Again in `_build_sync_subsystem` with the same DB callable, plus
   `provider=ctx.archive` and `ai_store=ctx.ai_store` — replacing the
   instance bound under `ctx.cache_inspector` / `ctx.cache_actions`.

The plan's §4.2 finding 1.4.4 flagged this as a "is this the early or
late inspector?" confusion: any code that grabbed a reference during
boot (none today, but easy to introduce) would be silently working
against a discarded instance. It also tripled the surface area of the
"do I assert ctx.cache_actions is not None?" question.

PR H also asked us to apply the **deletion test**: would inlining
either module into its callers make the code simpler?

- `CacheInspector` callers (non-test): `routes/cache.py` (summary, list_orphans,
  status_for_clip), `routes/pages/clips.py` (per-clip badge + popover),
  `services/cache_actions.py` (consults invariants before mutating).
  Inlining would force every consumer to re-implement the
  three-layer batched fetch (`_load_metadata`, `_load_media_local`,
  `_load_media_ai`, `_load_pins`, `_load_pending_counts`) plus the
  `LayerStatus` assembly. **Deletion test fails — keep.**
- `CacheActions` callers (non-test): `routes/cache.py` (POST evict
  endpoints) and `services/lru_eviction.py` (background tick calls
  `evict_local_media` per row). The invariant-enforcement loop
  (inspect → skip-and-log / mutate-and-log → return EvictOutcome) is
  load-bearing and re-used by LRU. **Deletion test fails — keep.**

## Alternatives

- **Option A (chosen).** Construct once in `_build_cache_subsystem`
  with `provider=None` / `ai_store=None`; expose
  `attach_provider(provider, *, host_local_proxies)` and
  `attach_ai_store(ai_store)` mutators that `_build_sync_subsystem`
  calls after the archive subsystem has wired the deps.
- **Option B.** Reorder builders so `_build_cache_subsystem` runs
  **after** `_build_archive_subsystem` when `init_external=True`,
  taking the provider/ai_store as parameters. Cleaner shape in the
  online case but ugly in the offline case — `_build_cache_subsystem`
  needs the reconciler-and-services flow to happen regardless of
  external init, so we'd end up either splitting the builder in two
  or passing None down the same path. The mutator approach below
  keeps both call sites obvious.
- **Option C.** Construct the cache services *inside* the
  `AppContext` dataclass via `default_factory`, then `attach_*` from
  the builders. Rejected: factories can't see `ctx.db` until after
  `_build_core` runs, and we'd need a placeholder DB callable that
  would mask wiring bugs.

## Decision

Option A. Both `CacheInspector` and `CacheActions` now expose
`attach_provider` / `attach_ai_store` no-arg-required mutators.
`_build_sync_subsystem` no longer re-binds `ctx.cache_inspector` or
`ctx.cache_actions`; it asserts the instances are present (they will
be, after `_build_cache_subsystem`) and calls the attach methods.

**Acceptance criterion verified:** `id(ctx.cache_inspector)` and
`id(ctx.cache_actions)` are identical before and after
`_build_sync_subsystem` runs, in both the offline-forced and
fake-CatDV online boot paths.

Neither module was deleted — both pass the deletion test, and even if
they didn't, ripping a 400+ LOC pure-DB service with its own
integration tests is a separate undertaking from this construction
cleanup.

## Consequences

- `ctx.cache_inspector is ctx.cache_inspector` for the lifetime of
  the context — code that captures a reference at any boot phase
  observes the same object the routes see.
- The attach methods are deliberately re-callable (idempotent: just
  re-set the private attr) so future test harnesses can swap the
  provider mid-flight without restarting the whole context. They
  are not currently called from anywhere except the sync builder.
- A future cleanup could push `provider` and `host_local_proxies`
  back into the constructor and reorder the builders (Option B
  above) — but only if a use case appears that wants the cache
  services constructed *strictly* after the archive subsystem. No
  such case exists today.
- The `attach_*` methods type their arguments as `Any | None` to
  avoid pulling `ArchiveProvider` / `AIInputStore` imports into
  `cache_inspector.py` / `cache_actions.py`; both modules
  deliberately don't depend on the archive package (they only know
  about the DB shape). The constructors already had this typing for
  the same reason.
