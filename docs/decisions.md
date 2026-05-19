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

## 2026-05-19: PR 6 — cache-layer signal sources, audit semantics, and LRU safety

**Context:** PR 6 adds the read-only `CacheInspector` and the mutating
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

**Alternatives:** (a) Add a `last_accessed_at` column to `clip_cache` and
write to it on every cache read; or use `provider_etag` change time. (b)
Hard-code `"system"` everywhere; or introduce a thin `User` placeholder
record now. (c) Run all three layers regardless of skips (forgiving the
chain); or evict metadata first so the rest can be diagnosed from the
inspector. (d) Best-effort: cross a pin if the cap is breached; or hard
fail with an exception. (e) Quiet: only successes are noteworthy. (f)
Always call `provider.get_clip()` per orphan check.

**Choice:** (a) `clip_cache.fetched_at` doubles as `last_used_at` for the
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

**Why:** (a) Adding a `last_accessed_at` column would mean updating it
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

## 2026-05-19: PR 7 — Filesystem archive adapter

**Context:** PR 7 ships the second `ArchiveProvider` adapter
(`FilesystemArchiveProvider`) plus the shared contract test suite, closing
the seven-PR migration. Six design calls had to be made: (a) how
`provider_clip_id` is derived from the on-disk layout; (b) what counts as a
"catalog" when subdirectories are present; (c) the policy when `ffprobe`
is absent; (d) how timecodes are encoded inside the sidecar; (e) whether
the FS adapter offers a real etag; (f) whether the FS adapter writes
through to `clip_cache` / `field_def_cache`.

**Alternatives:** (a) `sha256` of the absolute path (opaque), the
absolute path itself (leaks `FS_ROOT`), or the filename alone (collides
across catalogs). (b) Each leaf directory could be its own catalog
(deep tree → fragmented UI), or recursion could be forbidden (forces
flat layout on the user). (c) Refuse to start without `ffprobe`
(blocks hobbyist installs), or treat probe failure as fatal per-clip
(noisy in degraded states). (d) Persist canonical SMPTE `txt` plus
`secs`+`fps` (redundancy + drift on fps change), or `secs` alone
(loses anchor against a future fps redetection). (e) Skip etags and
match CatDV's heuristic-only mode (would forfeit the cheap, correct
write-time concurrency that POSIX file ops give us). (f) Write through
to the existing cache mirrors (extra invalidation surface for no
latency win on local I/O).

**Choice:** (a) `provider_clip_id` is the path of the media file
relative to `FS_ROOT` with the media extension stripped and OS
separators normalised to `/`. Example:
`FS_ROOT/archive_30s/clip001.mov` → `"archive_30s/clip001"`.
(b) A **catalog is a top-level directory under `FS_ROOT`**.
Subdirectories below contribute to `provider_clip_id` via recursion
but are not separate catalogs. Hidden directories (those starting with
`.`) and the literal `.archive` directory are excluded from the
catalog list. (c) `ffprobe` is optional: when `shutil.which("ffprobe")`
is `None`, `media_probe.probe()` logs a single warning per process and
returns `(duration_secs=0.0, fps=25.0)`. Subprocess failures or
malformed `ffprobe` JSON also fall back to defaults — the user can
still annotate; only timeline display will be inaccurate.
(d) Timecodes are persisted as `{"secs": float, "frm": int, "fps":
float}` triples with `frm = round(secs * fps)`. The canonical SMPTE
`txt` string is dropped on write — it is a display concern derivable
from `secs + fps`. (e) The FS adapter is etag-aware
(`supports_etag=True`); etag = SHA-256 of the sidecar bytes on disk;
missing sidecar = etag `None`. Writes that supply a stale etag return
`WriteResult(status="conflict", ...)` without touching disk. (f) The
FS adapter accepts `clip_cache_repo` / `field_def_cache_repo` / 
`db_provider` kwargs for registry-symmetry but ignores them.

**Why:** (a) Path-derived ids are human-readable in the audit log,
unambiguous within a `FS_ROOT`, and survive cross-platform deploys
because we normalise the separator on the way in. (b) A one-level
catalog model matches the existing CatDV pane's switcher and lets
users still organise within a catalog by subdirectory without
exploding the UI. (c) The probe path is the only place that needs
external tooling; gating startup on it would block legitimate
deployments (test rigs, lightweight installs). One warning is enough
diagnostic — repeated warnings would spam the log. (d) Storing both
`secs` and `frm` lets a future fps-redetection migrate timelines
deterministically; storing `txt` adds drift potential without buying
anything the renderer cannot regenerate. (e) The atomic-rename write
path makes a SHA-256 etag both cheap and correct — every successful
write changes it, every conflict refuses cleanly. This is what the
spec wants and what CatDV cannot do today. (f) Sidecars are the cache:
the disk read is sub-millisecond, the JSON parse is fast, and the
canonical clip is reconstructed from on-disk truth every time. Adding
a second mirror introduces an invalidation surface (sidecar edited
outside the app, cache says otherwise) for no latency win.
