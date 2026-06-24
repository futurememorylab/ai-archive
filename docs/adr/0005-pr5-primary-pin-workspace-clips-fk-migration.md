# 0005. PR 5 — primary pin vs. workspace_clips, FK migration, no fetch_media

- **Date:** 2026-05-19
- **Status:** Accepted
- **Lifespan:** Feature

## Context

PR 5 adds `workspaces` + `workspace_clips`, the `WorkspaceManager`
lifecycle service, and the four offline-cycle UI surfaces (connection pill,
workspace switcher, sync drawer, per-clip queued badge). Three design calls
had to be made: (a) `clip_cache.pinned_to_workspace_id` is a single integer
FK while a clip can in principle belong to multiple workspaces, so the
column can't be the source of truth; (b) attaching the FK on
`clip_cache.pinned_to_workspace_id` to the brand-new `workspaces(id)` table
is not supported by SQLite's `ALTER TABLE`; (c) the spec talks about
`provider.fetch_media()` but the codebase already has a working
`proxy_resolver.path_for_clip_id()` doing exactly that.

## Alternatives

(a) Promote `pinned_to_workspace_id` to a JSON column or a
join table that lives on `clip_cache`. (b) Defer the FK to a v3 migration —
leave the column as a bare INTEGER. (c) Add `fetch_media` to the
`ArchiveProvider` Protocol and reimplement the same logic inside the CatDV
adapter.

## Decision

(a) `clip_cache.pinned_to_workspace_id` is treated as the
*primary* pin (last-set-wins) and is maintained as a write-through from
`WorkspaceManager.add_clips` / `prepare` / `release`. `workspace_clips` is
the source of truth: `WorkspacesRepo.workspaces_pinning(clip_key)` returns
the full list of workspaces pinning a clip, and PR 6's cache-evictability
invariants will read it. (b) The migration uses the SQLite table-rebuild
idiom: rename `clip_cache` to `clip_cache_old`, create the new `clip_cache`
with `REFERENCES workspaces(id) ON DELETE SET NULL`, copy rows over, drop
the old table, and recreate the catalog index. SQLite foreign keys are
*not* enabled by aiosqlite by default; we still write the FK so any test
or future migration that turns them on (e.g. via `PRAGMA foreign_keys = ON`)
gets the cascade-set-null behaviour for free. (c) Workspace prep calls
`proxy_resolver.path_for_clip_id(int(clip_id))` directly, gated by
`provider.capabilities.media_is_local`. The proxy resolver already caches
to the right directory and is the path the media route uses; adding
`fetch_media` would have doubled the surface for zero new behaviour.

## Consequences

(a) Single-column FKs are easy to reason about in the query
planner; an N-pin question is rare enough (PR 6's "pinned by which
workspaces?" UI is the only consumer) that a small `GROUP BY` on
`workspace_clips` beats reshaping the cache row. The pin column is still
useful as a fast "is this clip pinned at all?" check on the cache row.
(b) The rebuild is the standard SQLite idiom for attaching constraints to
existing columns; the migration test inserts a `clip_cache` row before
applying 0005 and asserts it survives. (c) The two abstractions (archive
provider vs. proxy-bytes locator) are already cleanly separated in the
codebase — coupling them just because the spec called the verb
`fetch_media` would have been a step backward.

Workspace `release()` is non-destructive (spec §9.5 rule 5): it drops the
`workspace_clips` rows and clears or re-points the primary pin, but
does NOT delete `clip_cache` rows or proxy files. LRU eviction (PR 6) is
the only path that reclaims disk; the explicit user action for immediate
reclamation is also PR 6.
