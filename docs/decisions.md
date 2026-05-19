# Architecture Decisions

Each decision: one paragraph — context, alternatives, choice, why. Append below.

## 2026-05-18: Python-only stack, no Node frontend

**Context:** The PoC (Archive-AI) used a Node/React/TS stack. Maintaining two
package.json files, two test runners, and TS↔Python type drift consumed
significant time.

**Alternatives:** React+TS SPA via Vite, Svelte SPA.

**Choice:** Server-rendered Jinja2 + HTMX + Alpine.js + Tailwind standalone CLI.
The UI is forms + one video screen; React is overkill.

**Why:** One language top to bottom, no npm/Node, no build step beyond Tailwind
CLI, smaller cognitive surface for future single-maintainer work.

## 2026-05-19: AIInputStore port distinct from ArchiveProvider

**Context:** Vertex AI Gemini needs media bytes available at a URI it can
read (today: GCS). The same clip's bytes can live on a CatDV server
(archive), on the annotator host's disk (proxy cache), and in a GCS bucket
(AI input). Conflating "where the archive is" and "where Gemini reads from"
would force a CatDV install and a filesystem-archive install to share the
same upload code, and would make adding the Gemini Files API a rewrite of
the annotator rather than a new adapter.

**Alternatives:** Merge AI upload into ArchiveProvider; rename
`GcsService` to a more abstract `MediaCdn` without a Protocol.

**Choice:** Introduce `AIInputStore` Protocol parallel to `ArchiveProvider`,
with adapter packages under `backend/app/archive/ai_stores/`. The GCS
adapter ships today; a Gemini Files API stub proves the Protocol shape.

**Why:** Two ports with one responsibility each beats one port with two
responsibilities. Switching the AI store is one adapter swap; switching
the archive is another; neither cascades into the worker.

## 2026-05-19: PR 3 — single migration file, clip TTL keyed off CanonicalClip.fetched_at

**Context:** PR 3 adds `provider_id`/`provider_clip_id` to six clip-keyed
tables and creates two new mirror tables (`clip_cache`, `field_def_cache`).
Two design calls had to be made: (a) whether to split into two migration
files (one for ALTER TABLEs, one for new tables) or keep them together; and
(b) whose "now" wins when stamping `clip_cache.fetched_at` — the repo's
own `datetime.now()` at write time, or the `CanonicalClip.fetched_at` the
adapter computed via its injected clock.

**Alternatives:** (a) Split migrations 0003 (provider columns) and 0004
(cache tables); use the repo's own clock for `fetched_at`. (b) Use the
adapter's clock end-to-end so tests can advance time deterministically.

**Choice:** (a) Single file `0003_provider_id_and_caches.sql` — the changes
are conceptually one ("provider-aware identity") and the rollback boundary
should stay tight. (b) The repo writes `clip.fetched_at` (already computed
by the adapter from its own clock) into the row, rather than calling
`datetime.now()` again. Field-def cache uses `replace_all_for_provider`
with `_now_iso()` internally because there is no per-row "fetched_at" on
the canonical `FieldDef`; tests of TTL expiry there would need a different
fixture.

**Why:** Two migrations doubled the test surface without buying anything.
Using the adapter's clock for `clip_cache.fetched_at` makes TTL expiry
testable with an injected clock — important for the offline-mode work in
later PRs where time-based behaviour must be deterministically exercised.

## 2026-05-19: PR 4 — enqueue is atomic with mark_applied; conflict locus is the adapter

**Context:** PR 4 introduces the `pending_operations` journal and turns the
"Apply accepted" route into an enqueue. Two design calls had to be made:
(a) what to do about a user double-clicking Apply (the second click must
not enqueue duplicates of ops that the first one already wrote), and (b)
where to detect conflicts — in the SyncEngine, in the WriteQueue at enqueue
time, or inside the provider adapter.

**Alternatives:** (a) Filter inside `review_items_repo.list_by_clip` to
exclude rows with `applied_at IS NOT NULL`; or rely on a UNIQUE constraint
on `pending_operations` keyed by review-item-id. (b) Detect conflicts in
the engine by comparing the queued `expected_etag` against a refreshed
`clip_cache` row before calling `apply_changes`.

**Choice:** (a) `ReviewItem` gains an `applied_at` attribute, repos expose
it, and `WriteQueue.enqueue_apply` filters `it.applied_at is None`
*inside its own transaction*, then writes the `pending_operations` rows
and `mark_applied` in one `commit()`. A double-click can't race because
both code paths see the same DB state. (b) Conflict detection lives only
inside the adapter (`CatdvArchiveAdapter.apply_changes`): it captures
`modifyDate` as the pseudo-etag and short-circuits with
`WriteResult(status="conflict", conflict_detail=…)` on drift. The engine
treats `WriteResult.status` opaquely.

**Why:** (a) Putting the dedup inside the queue keeps the route ignorant
of the journal and avoids a schema-level uniqueness rule that would force
us to commit to a "one op per review-item" mapping forever (markers
already collapse N items into one op). (b) The adapter is the only thing
that knows how to compute a pseudo-etag for its backend — pushing that
knowledge into the engine would couple the engine to the CatDV-specific
`modifyDate` quirk. Engines downstream of the FS adapter (PR 7) will use
sha256-based etags through the same code path with no engine change.

## 2026-05-19: PR 5 — primary pin vs. workspace_clips, FK migration, no fetch_media

**Context:** PR 5 adds `workspaces` + `workspace_clips`, the `WorkspaceManager`
lifecycle service, and the four offline-cycle UI surfaces (connection pill,
workspace switcher, sync drawer, per-clip queued badge). Three design calls
had to be made: (a) `clip_cache.pinned_to_workspace_id` is a single integer
FK while a clip can in principle belong to multiple workspaces, so the
column can't be the source of truth; (b) attaching the FK on
`clip_cache.pinned_to_workspace_id` to the brand-new `workspaces(id)` table
is not supported by SQLite's `ALTER TABLE`; (c) the spec talks about
`provider.fetch_media()` but the codebase already has a working
`proxy_resolver.path_for_clip_id()` doing exactly that.

**Alternatives:** (a) Promote `pinned_to_workspace_id` to a JSON column or a
join table that lives on `clip_cache`. (b) Defer the FK to a v3 migration —
leave the column as a bare INTEGER. (c) Add `fetch_media` to the
`ArchiveProvider` Protocol and reimplement the same logic inside the CatDV
adapter.

**Choice:** (a) `clip_cache.pinned_to_workspace_id` is treated as the
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

**Why:** (a) Single-column FKs are easy to reason about in the query
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
