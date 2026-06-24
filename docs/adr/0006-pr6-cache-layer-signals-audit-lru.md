# 0006. PR 6 — cache-layer signal sources, audit semantics, and LRU safety

- **Date:** 2026-05-19
- **Status:** Accepted
- **Lifespan:** Invariant

## Context

PR 6 adds the read-only `CacheInspector` and the mutating
`CacheActions` service plus an LRU eviction background task. Six design
calls had to be made: (a) where does "last-used" come from for the
`metadata` layer (no per-row access column exists); (b) what goes in
`cache_actions_log.who` when there is no auth surface yet; (c) what is
the layer order in `evict_clip_everywhere` and what happens to layers
already past in the chain when a later one is blocked; (d) what does the
LRU task do when the pinned subset alone already exceeds the cap; (e)
should `cache_actions_log` rows be written for skips, or only for
successful evictions; (f) how should `list_orphans` define "orphan" —
does it have to call the upstream provider for every clip on every
refresh?

## Alternatives

(a) Add a `last_accessed_at` column to `clip_cache` and
write to it on every cache read; or use `provider_etag` change time. (b)
Hard-code `"system"` everywhere; or introduce a thin `User` placeholder
record now. (c) Run all three layers regardless of skips (forgiving the
chain); or evict metadata first so the rest can be diagnosed from the
inspector. (d) Best-effort: cross a pin if the cap is breached; or hard
fail with an exception. (e) Quiet: only successes are noteworthy. (f)
Always call `provider.get_clip()` per orphan check.

## Decision

(a) `clip_cache.fetched_at` doubles as `last_used_at` for the
metadata layer — the UI label says "Cached" to match. The TTL logic
already keys off `fetched_at`, so the user's mental model is consistent.
(b) `who` is the literal `"system"` for LRU evictions and
`"request"` for user-driven routes. The column is plain TEXT so a future
auth layer can replace `"request"` with a stable user identifier with no
schema change. (c) `evict_clip_everywhere` orders calls as
`media-ai → media-local → metadata`, short-circuiting on the first
invariant skip unless `force=True`; with `force=True` the order is
unchanged but every layer is attempted regardless and a prominent
`evict_clip_everywhere_force` audit row is written in addition to the
per-layer entries. (d) LRU never crosses a pin: if the non-pinned bytes
total is already below cap the task is a no-op; if evicting all
non-pinned rows would still leave the total over cap, the sweep logs a
`partial` row and emits a warning. The pinned-bytes-alone-exceeds-cap
case is a deployment misconfiguration the operator must resolve by
releasing workspaces or raising the cap. (e) Skips ARE logged.
"Why didn't this evict?" is itself diagnostic information; a missing
log entry would force the operator to re-run the action to find out.
The `detail` column carries the invariant name (e.g.
`"pinned_by_workspaces=[3,5]"`). (f) `list_orphans()` is cheap by
default: it returns rows whose `clip_cache` row is absent (a fast index
join). The expensive provider round-trip is gated behind an explicit
`deep=True` flag the route does not enable by default. This keeps the
`/cache` page snappy even when offline and avoids thundering the
provider on every refresh.

## Consequences

(a) Adding a `last_accessed_at` column would mean updating it
on every cache read across multiple call sites with no observable user
benefit beyond a marginally more accurate "age" display; `fetched_at`
is good enough. (b) Wiring a `User` placeholder now would put a fake
abstraction in front of every cache action and have to be undone or
extended when real auth lands. A literal string buys the same audit
shape with no abstraction debt. (c) The short-circuit matches the spec
§9.5 intent: metadata is preserved for diagnosis when an earlier layer
is blocked, but `force=True` is the explicit "I know what I'm doing"
hard-delete the spec calls out. (d) Crossing pins would invalidate the
workspace contract; failing hard would make the LRU task fragile. A
warning-with-partial log entry surfaces the misconfiguration without
breaking the loop. (e) The audit log is the only persistent record of
"the system wanted to evict X but couldn't" — losing that information
makes operator debugging harder. (f) The expensive case (calling the
provider per clip) is exactly the work the workspace-prep flow already
does; doing it again on every orphan check would multiply CatDV REST
calls for no real-world benefit (a clip moves from "present" to
"deleted" in CatDV rarely, and the deep check is available when an
operator wants to run it).
