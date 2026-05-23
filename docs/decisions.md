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

## 2026-05-20: UI MVP — five decisions

**Context:** First UI deliverable. Backend was complete through PR 7; only HTTP/JSON surfaces existed. Spec at `docs/specs/2026-05-20-ui-mvp-design.md`.

**1. Defer Tailwind to ≥4 screens.** ADR 2026-05-18 nominated Tailwind standalone CLI, but two read-only screens don't justify a build step. Ship hand-crafted `static/app.css` (~280 lines) with CSS variables. Adopt Tailwind when Templates + Jobs + Archive land.

**2. HTML routes call `ctx.archive` directly.** The HTML layer reuses the same `ArchiveProvider` adapter as `/api/catdv/*`, in-process, without going through HTTP. The JSON API stays as a public surface for future external consumers; the HTML layer is parallel, not a client. Avoids a second JSON serialization and a network hop on every render.

**3. Native `<video controls>` for MVP playback.** Custom transport (J/K/L, ±1 frame, set in/out) is review-flow work; premature here. Native controls cover play/pause/scrub/volume/fullscreen for free. Add custom transport when the AI-review UI lands.

**4. View-model adapter (`backend/app/ui/view_models.py`) keeps templates logic-free.** Templates receive flat dicts (`clip_summary`, `clip_detail`). Reading `provider_data` shape in Jinja is a maintenance trap — provider-specific keys (`bigNotes`, `media.codec`, …) belong in one Python function, not in three template files. When the FS adapter lands, only `view_models.py` adapts.

**5. Dark theme only.** All colors flow through CSS variables (`--bg`, `--panel`, `--accent`, …). Light theme is a ~20-line later addition; the tokens are ready for it.

## 2026-05-20 — Media prefetch + cache UI wiring (PR 8)

1. Prefetch is a persistent SQLite queue (`prefetch_queue`), not in-memory. A
   long download must survive process restart. The same table powers the
   `/cache?tab=queue` UI panel.

2. Single-flight serialization lives in the worker, not in `RestProxyResolver`.
   The resolver remains request-driven; the prefetcher runs at most one
   `tick_once()` body at a time. On-demand `/api/media/{id}` requests do not
   queue behind it — the existing "file exists, skip download" check de-dups
   naturally once the file lands.

3. `RestProxyResolver` now records into `proxy_cache` after a successful
   download. Without this, `CacheInspector` reports `media-local: absent`
   even when the file is on disk. The prefetcher would have papered over
   this; we fix the underlying gap instead.

4. Cancellation is honored only for `queued` and `error` rows. A
   `downloading` row cannot be cancelled mid-stream — we do not want
   partial files that `curl -C -` would later treat as a resume target.
   `stop()` is still respected between rows.

5. Cache badges in the clips list are rendered server-side from a single
   bulk `CacheInspector.status_for_clips([keys])` lookup, not via per-row
   HTMX. The `/ui/cache-badge/{provider}/{clip_id}` route stays for
   post-evict refresh but is no longer the primary render path.

6. No new column on `proxy_cache`. The queue table's `status` is the queue's
   job. Once a file lands, `proxy_cache.record()` is called and the queue
   row goes to `done`. The two tables are joined on
   `(provider_id, provider_clip_id)` only at display time.

## 2026-05-21 — Prompt management: replace templates with versioned prompts

**Context:** Annotation prompts lived in a single mutable `templates` row
(`name`, `description`, `prompt`, `output_schema`, `target_map`, `model`,
`archived`). No versions, no way to freeze a known-good prompt while
iterating, no first-class management UI. The Claude Design mockup
(`screens.jsx` TemplatesScreen) proposed a list/detail layout with per-version
state pills, model picker, and kebab actions. Six design calls had to be
made: (a) whether to keep `templates` as a compatibility view or replace it
outright; (b) whether the title+description belong to the prompt identity or
to each version; (c) what to do with `output_schema` (the design omits it,
but the annotator needs it); (d) edit semantics for production versions; (e)
whether kebab "Archive" archives the whole prompt or just the current
version; (f) REST verb style for state mutations.

**Alternatives:** (a) Add `prompts` + `prompt_versions` alongside `templates`,
keep the old table as a denormalized "current production" mirror so the
annotator keeps working unchanged; or add `parent_prompt_id` + `version_num`
+ `state` columns to `templates` (smallest schema delta). (b) Title +
description per-version, snapshotted alongside body (more flexibility, but
the list rail has to pick a version's title and "this prompt" becomes
ambiguous). (c) Hide `output_schema` from the UI but keep it in storage; or
remove it entirely and derive what the annotator needs from `target_map`.
(d) Allow in-place edits on production with no auto-archive (closest to
current behavior); or allow edits only on drafts but require manual archive
of the previous prod when promoting. (e) Archive just the current version
(kebab on prompt, but version-level effect); or two distinct menu items.
(f) `POST /actions/promote` REST-pure form; or PUT with a state field in the
body.

**Choice:** (a) Replace `templates` outright. Migration 0009 creates
`prompts` + `prompt_versions`, backfills each existing row as v1@production,
rebuilds `annotations` and `jobs` to use `prompt_version_id`, then drops
`templates`. Annotator/jobs/review/seed all rewired in the same change.
(b) Title + description belong to the prompt (per-prompt UNIQUE name);
versions carry only body + target_map + output_schema + model + state.
(c) `output_schema` stays as a third editable JSON panel in the detail view,
versioned alongside body and target_map. (d) Production is immutable —
editing forces creation of a new draft; promoting a draft atomically
demotes the previous production to `archived` in a single transaction. A
partial unique index `idx_one_prod_per_prompt` enforces ≤1 production per
prompt at the database level even if the repo were bypassed. (e) Kebab
"Archive" archives the whole prompt (soft delete, surfaced in
`/prompts/archived`); version-level `archived` is a consequence of promote,
not a user action. (f) Verb-style sub-paths (`:archive`, `:promote`,
`:duplicate`, `:restore`) for state mutations to keep them visually distinct
from RESTful CRUD.

**Why:** (a) Keeping `templates` as a compat mirror would leave a dead table
in the schema forever and require sync code on every promote — the mental
model of two tables doing the same job is worse than the one-time cost of
rewriting the annotator. The user explicitly asked for the clean rewrite.
(c) The annotator needs the JSON schema to constrain Gemini's response;
hiding it would mean new prompts can't be created end-to-end from the UI.
Deriving it from `target_map` lacks enough information. Three editor panels
in the same x-data scope is straightforward. (d) Mutation invariants
guarantee an annotation's `prompt_used` text cannot silently change after
the fact — critical for reproducibility. The partial unique index is
belt-and-suspenders defense against a hypothetical race or future refactor
that bypasses the repo's transaction. (e) The "Archived prompts" view is a
spec requirement; per-prompt archive is what the user asked for. Two
separate "Archive version" + "Archive prompt" entries would add UI noise
nobody asked for. (f) The other half of the codebase already uses
verb-style sub-paths for state mutations (see `routes/cache.py`); matching
that pattern keeps the API consistent.

**Implementation deviations from the spec (kept consistent across the
codebase):** (i) The page-action endpoints (`/prompts/{id}/_new_version`,
`_promote`, `_duplicate`, `_archive`, `_restore`) are plain POST → 303
redirect, not HTMX partials. The spec described HTMX hot-swaps but the
implementer found that 303-redirect-on-mutation matches the project's
established pattern and avoids hooking up partial-render endpoints for
five different result shapes. (ii) Save in the editor calls `/api/...`
directly via `fetch()` then `window.location.reload()` instead of an
HTMX-partial swap of the detail pane. Loses no information, simpler, but
discards client scroll position on save. (iii) `_target_map_to_json` uses
`model_dump_json(exclude_unset=True)` so a `TargetEntry` with only the
required fields (`kind`, plus `identifier`/`target` when applicable)
round-trips without spurious nulls — the test for export-shape depends on
this.

**Bugs caught in post-implementation review:** `VersionEdit.target_map` and
`PromptCreate.target_map` were originally typed `dict`, which let invalid
shapes persist and then break the detail view on the next GET (the model
constructor would raise on read). Both now use `TargetMap` so validation
happens at the route boundary. `promote_version` originally would silently
revive an archived version into production — now rejects with
`VersionImmutableError` unless target is `draft`, and is idempotent on
already-production. `create_version` originally accepted a
`from_version_id` belonging to a different prompt — now rejects with
`LookupError`. Page actions originally returned 500 on unknown IDs — now
return 404. The migration originally did not preserve `provider_id` /
`provider_clip_id` (added by 0003) through the `annotations` rebuild —
now does, with a test that explicitly asserts it.

## 2026-05-21 — Prompt management: post-merge polish (styling, alpine init, duplicate dialog)

**Context.** First hands-on session with the shipped Prompts UI surfaced
three issues: (a) the body textarea and the `<button>`-based "model" and
"version" pills rendered with the browser-default white background on the
dark theme — the only existing CSS rule for `.txt` was a `.filters-form
input` rule scoped to a different page; (b) the kebab menu and model
picker were dead — Chrome console showed `menuOpen is not defined`,
because `promptEditor.js` was loaded inside `{% block body %}` (after
Alpine) but registered an `alpine:init` listener that had already fired;
(c) "Duplicate" was a one-click form post that always produced "Copy of
X", giving no chance to rename or adjust the description for the new
prompt.

**Alternatives.** For (a): add a generic `.txt` rule, scope textarea
styling under `.prompts-page`, or set inline styles in the template.
For (b): add a `{% block head_scripts %}` to layout.html and override per
page, restructure `promptEditor.js` to call `Alpine.data(...)` directly if
Alpine is already on the page, or load it in `<head>` next to player.js.
For (c): a separate `/prompts/{id}/duplicate` form page, an inline
expanding section in the detail pane, or a modal dialog.

**Choice.** (a) A generic top-level `.txt` rule (dark bg, light text,
focus highlight, read-only state) since `.txt` is project-internal and
used only on the prompts pages; plus `button.tag { background:
transparent }` so any future `<button class="tag …">` inherits the
correct look. (b) Add `promptEditor.js` to the head `<script defer>`
chain *before* the Alpine bundle — matches the established `player.js`
pattern and the explicit comment in `layout.html` ("listener must
register first"). (c) A modal dialog with Name + Description fields,
opened from the kebab via `openDuplicate()`, submitted by `fetch()`. On
422/409 the modal stays open and shows an inline error pill; on success
the response's 303 redirect is followed by the browser.

**Why.** (a) The "fix it via inline style" path would scatter
background/color tokens across templates — Tag inline styles are already
used for layout, but theming belongs in CSS where the design tokens live.
(b) Either restructure-loading-order or restructure-the-listener works;
loading-order is one line and consistent with the existing convention,
so future scripts that need `alpine:init` have an obvious place to go.
(c) `PromptsRepo.duplicate` already had the "next-available `Copy of X`"
walker; the new contract is: `name=None` → keep the walker (preserves
the existing tests and the API's REST-default behavior), `name=...` →
use as-is and let `aiosqlite.IntegrityError` surface as 409. The page
action returns 409 JSON instead of 303 only on the failure path so the
modal can keep the user's typed values; the success path is still 303
to `/prompts/{new_pid}` (matches every other page action in the file).
The dialog approach matches what users expect from "Duplicate…" with an
ellipsis affordance — explicit on the menu item.

**Out of scope (deliberately).** Did not refactor the Alpine component to
register itself idempotently regardless of script order — load order is
the smaller, more localized fix. Did not rename `.txt` to something more
descriptive (e.g. `.field-input`) — that's a follow-up across the
templates that touch it, not a one-line CSS change.

## 2026-05-21 — Clip Annotate UI: Draft view, scope toggle, in-page annotate flow

**Context.** Backend annotation pipeline (GCS → Gemini → annotations +
review_items) was already complete, but the clip detail page had no
entry point to fire a prompt against the open clip or read the result.
Spec at `docs/specs/2026-05-21-clip-annotate-ui-design.md`; plan and
17-task execution at `docs/plans/2026-05-21-clip-annotate-ui.md`.

**Alternatives & choices.**

- *View-model shape.* Plan originally suggested pydantic `DraftView` /
  `MarkerView` / `FieldView` models. Codebase convention is plain
  `dict[str, Any]` view-models (see `backend/app/ui/view_models.py`).
  **Chose plain dicts** in `backend/app/services/draft_view.py` to match.
- *Where prompt-name / version-num come from.* Could fetch inside
  `build_draft_view` (introduces repo dependency) or take as
  caller-supplied kwargs. **Chose caller-supplied** keyword-only kwargs;
  the route helper `_build_draft_for_clip` does the prompt lookup, the
  view-model stays pure.
- *Production-prompt filter.* `_prompt_envelope` was already exposing
  `current_production_version_id`. The dropdown calls `GET /api/prompts`
  (list endpoint, returns bare prompt rows — not envelopes). **Extended
  `list_prompts` to enrich each row** with both `current_production_version_id`
  and `current_production_version_num`, and added `_version_num` to the
  envelope for consistency. The dropdown then filters client-side.
- *Sharing run state between dropdown and aside.* Two siblings under the
  `.detail` wrapper. **Lifted `scope, tab, running, runningPromptName,
  runStatus, runError, jobId` onto the root** via
  `x-data='Object.assign(player(...), { ... })'` so both children
  read/write through `$root.*`. The dropdown's Alpine factory keeps
  only its own UI state (`open`, `prompts`, `loading`, `error`);
  the `pick(prompt, root)` method takes `$root` and mutates it.
- *Partial route's clip dependency.* `_anno_panels.html` uses `clip.fps`
  for SMPTE timecodes. The new `GET /clips/{id}/draft` partial route
  doesn't have a populated `clip` to pass. **Added `panels.fps` as a
  partial-local override**: `{{ smpte(m.in_secs, panels.fps or clip.fps) }}`.
  Published path leaves `panels.fps` unset (falls through to `clip.fps`),
  Draft path passes `clip.fps or 25.0` explicitly.
- *Empty-state marker.* The Draft empty body carries
  `data-draft-empty="true"` on its own element; the integration test
  asserts presence/absence of that string. Avoids parsing rendered
  HTML structure.
- *Annotation `created_at` round-trip.* The DB column already existed
  (written at INSERT) but the model and SELECTs didn't read it back.
  **Made it additive** — added `created_at: str | None = None` to
  `Annotation`, added the column to both SELECTs, and the `_row`
  mapper reads it with a `len(row) > 10` guard so older callers
  pulling fewer columns wouldn't break.
- *SSE error fallback.* When `EventSource.onerror` fires we close the
  stream and switch to polling `GET /api/jobs/{id}` every 2 seconds
  until terminal status. Loop is guarded by `root.running` so a
  successful SSE swap (which sets `running=false`) collapses the
  polling loop on the next tick. **Trade-off accepted:** the loop
  doesn't cap retry attempts, so a persistently-500 job endpoint would
  loop forever — bounded by `root.running` being flipped elsewhere.
- *Test approach.* `tests/integration/conftest.py` only provides a `db`
  fixture; the plan's `httpx.AsyncClient` + `client/ctx/seeded_clip_101`
  fixtures don't exist. **Followed the existing
  `tests/integration/test_routes_pages.py` pattern**: sync `TestClient`
  via a per-file `_make_client(monkeypatch, tmp_path)` helper, and an
  `asyncio.new_event_loop()` driver for repo seeding against the
  running app's `ctx.db`. The end-to-end test in
  `tests/integration/test_annotate_ui_e2e.py` imports the existing
  `FakeArchive / FakeResolver / FakeAIStore` from
  `test_annotator_worker.py` rather than redefining them.

**Why.** The constraint shaping every call was "do not touch the
backend pipeline." The result is a thin glue layer: one pure-function
view-model, two new template partials plus a small refactor of the
existing aside, an HTMX partial route, and ~120 lines of Alpine JS.
Visual parity between Published and Draft is automatic because both
render through the same `_anno_panels.html`.

**Out of scope (deliberately, called out in spec).** Per-item
accept/reject, push to CatDV via `write_queue`, annotation history
picker, side-by-side diff, cancel button, raw-response tab,
`scripts/setup-gcp.sh` / `.env.example` / `DEPLOY.md` edits. Each is a
clear follow-up that can land on top of this surface without
redesigning what's here.

## 2026-05-22 — Clip list filters: Cache + Annotations dropdowns, local-first resolution

**Context.** The clip list page needed a more prominent search box and
two filters — `Cache: any|none|local|ai` and
`Annotations: any|for_review|applied|none|has_any` — plus a single
"Actions" dropdown replacing the three per-action bulk buttons.

Neither cache state nor annotation drafts live at CatDV: both are
local SQLite concerns (`proxy_cache`, `ai_store_files`, `annotations`,
`review_items`). CatDV's `list_clips` cannot accept them as a query
predicate.

**Alternatives considered.**

1. *Client-side filter over the current page* — Simplest. Apply filters
   in the browser to whatever 50 rows the route already fetched. **Rejected:**
   pagination becomes a lie ("5 of 50" when the page is mostly filtered
   out), and "for review" with one draft on the catalog's tenth page
   would show nothing on page 1.
2. *Fetch-all-then-filter* — When a filter is active, walk every
   CatDV page (catalog has hundreds of clips) into memory, enrich
   with cache/annotation status, filter, then paginate locally.
   Always correct. **Rejected:** the VPN is slow (~300–400 KB/s) and a
   filter toggle would block the page on a minute-long sync.
3. *Local-first when filters active* (chosen) — Derive a candidate
   `set[int]` of CatDV clip IDs from SQLite, hydrate each from the
   metadata cache (`clip_cache`) or a single `archive.get_clip` call,
   apply the text query, sort by name, paginate locally.

**Choice.** Option 3, with explicit acceptance of its blind spot:
"absence" filters (`cache=none`, `anno=none`) are bounded to the
**universe of clips we've already observed locally** — anything in
`clip_list_cache` pages plus any row in `clip_cache`, `proxy_cache`,
`ai_store_files`, `annotations`, or `review_items`. A clip that exists
upstream but has never been listed will not appear under those
filters until it shows up in a list page.

The filter resolver lives at
`backend/app/services/clip_list_filters.py` and returns
`set[int] | None` (None = no filter, caller takes the existing
CatDV-paginated path).

**Why.**

- *Speed.* Filter toggle is a handful of indexed SQLite queries —
  effectively instant. No CatDV round-trip unless a candidate clip
  isn't in the metadata cache, and even then it's at most `limit`
  per-clip fetches after pagination.
- *Honest pagination.* Total reflects the filtered set, so the pager
  numbers match what the user sees.
- *Minimal blast radius.* The no-filter path is byte-for-byte
  unchanged; only when `cache` or `anno` is non-default does the
  route branch to `_filtered_page`. Existing tests that exercise the
  default path keep passing without modification.
- *Documented limitation.* The "absence" blind spot is unavoidable
  without enumerating CatDV's full catalog on every toggle. It's a
  reasonable price given the workflow — users care about "what do I
  have local / what have I drafted", and those are positive-set
  queries that the SQL knows about precisely.

**Other UI decisions in the same change.**

- *Explicit search submit.* The old `hx-trigger="input changed
  delay:300ms"` autosearch was replaced with a single `<form>` that
  submits on Enter, on the new "Search" button, or on `<select>`
  change. **Why:** every typeahead keystroke against the slow VPN
  burned a CatDV round-trip; the user wanted a deliberate search.
- *Cache filter as single-select (not multi-toggle).* Despite the
  `independent toggles` framing in brainstorming, the user picked the
  simpler single-select dropdown variant. Avoids ambiguity around
  "show clips that have local OR ai cache" vs "have both" — there's
  exactly one selected value.
- *Actions split-button.* Three bulk buttons (`Cache view`,
  `Cache selected`, `Evict selected`) collapsed into one `Actions`
  dropdown with `Cache locally` and `Remove from local cache`. The
  `Cache view ›` link was dropped per spec; users get to the cache
  page from the left rail. Disabled state is driven by the same
  Alpine `count` selection counter the prior toolbar used.

## 2026-05-22 — Local-filesystem proxy resolution (deploy on the CatDV host)

**Context.** The current `RestProxyResolver` downloads each clip's web
proxy (~300 MB H.264) from `GET /catdv/api/9/clips/{id}/media` over
the WireGuard VPN (~370 KB/s sustained) into `data/cache/proxies/`,
then hands that file to Gemini ingestion. When the annotator runs on
the same machine as the CatDV server, both the download and the local
cache are pure overhead — the proxy already exists on the host's
filesystem, written there by CatDV's worker pipeline.

The blocker was simply not knowing where on disk. The clip JSON
exposes `media.filePath` (the **hires** ProRes path,
`/Volumes/ARECA/ARCHIV_SOUKROME_FILMOVE_HISTORIE/...`), but nothing in
the per-clip JSON tells us where the matching proxy lives. Probing
`GET /catdv/api/9/mediastores` answered that:

```
Hires: /Volumes/ARECA/ARCHIV_SOUKROME_FILMOVE_HISTORIE
       /Volumes/ARECA2/ARCHIV_SOUKROME_FILMOVE_HISTORIE
Proxy: /Volumes/ARECA/CatDV_Proxy            (pathType: proxy/web)
       /Volumes/ARECA2/CatDV_Proxy
```

Pairing is by `pathOrder` within a media store. The proxy file mirrors
the hires file's relative path under the swapped root (CatDV
convention; `extensions: null` on the proxy pathType confirms no
filename rewriting). `klientAI` (non-admin) is allowed to read
`/mediastores` — verified via a temporary debug passthrough route in
the running backend (since removed).

**Alternatives considered.**

1. *Same code, loopback CatDV.* Deploy as-is with
   `CATDV_BASE_URL=http://localhost:8080`. The existing `/clips/{id}/media`
   stream now runs at disk speed instead of VPN speed; cache still
   exists but fills in seconds. **Rejected (as the destination, kept
   as a fallback option):** still maintains a cache subsystem we
   wanted to eliminate, still burns a CatDV session seat for media
   bytes, still couples Gemini ingestion to CatDV uptime. Trivially
   simple (env-var flip) so it remains a viable rollback path.
2. *Stream proxy → Gemini without writing to disk.* Pipe the
   `/clips/{id}/media` response body straight into
   `ai_store.ensure_uploaded`. **Rejected:** `ai_store` is built
   around `Path` input; refactoring it to accept an async iterator
   touches the whole AI-store layer including the GCS-files repo,
   for a smaller upside than option 3.
3. *Read `media.filePath` directly and ingest the 16 GB ProRes
   original.* **Rejected:** Gemini upload time and token cost would
   balloon ~50×, and we'd be re-doing the transcode CatDV already
   performed. Only viable with an on-the-fly ffmpeg transcode, which
   is essentially rebuilding the proxy CatDV already has.
4. *Read the proxy file from disk via `/mediastores` mapping*
   (chosen). Map hires-root → proxy-root once at startup, swap
   prefixes per clip, hand Gemini the small H.264 directly.

**Choice.** Option 4. Implementation is a rewrite of the existing
`FilesystemProxyResolver` (whose previous `{root}/{clip_id}.mov`
template never matched any real CatDV deployment — it was speculative
scaffolding from PR 7) plus a new `MediaStoreMap` value object that
parses the `/mediastores` JSON. The `PROXY_SOURCE=filesystem` env
value already exists and remains the selector; `PROXY_FS_ROOT` and
`PROXY_PATH_TEMPLATE` are removed because the mapping is fetched from
the server. The hires→proxy pairing rule is "same `pathOrder` inside
the same media store"; `proxy` paths must have `target: "web"` (we
ignore the desktop-client proxy variant).

**Why.**

- *Eliminates the cache subsystem on this deployment.* No writes to
  `data/cache/proxies/`, no `proxy_cache` row recording, no LRU
  eviction pressure from media bytes. The cache code path stays in
  place for `PROXY_SOURCE=rest` (off-site dev, the VPN-bound mode);
  on-host deploys simply don't exercise it.
- *No CatDV media seat.* Metadata calls (lightweight, already
  per-clip cached) are the only CatDV traffic. The 2-seat limit
  stops being a concern when the human web client is also running.
- *Gemini ingest stays small.* Resolver returns the existing web
  proxy file — ~25–50× smaller than the ProRes original, same
  bytes Gemini was already receiving via the REST path.
- *Authoritative config.* Fetching `/mediastores` at startup keeps
  the mapping in sync if the admin reshapes storage; no `PROXY_FS_*`
  env vars to drift out of date.
- *Failure mode is loud.* If a proxy is missing on disk we raise
  `ProxyNotFound` rather than silently falling back to REST — that
  would re-introduce the cache + VPN dependency the deploy was
  designed to eliminate. Operationally, missing proxies match
  CatDV's own "media unavailable" state for that clip.

**Pairing details for `MediaStoreMap`.**

- Group `paths` by `mediaStoreID`.
- Within a store: collect `mediaType=hires` entries into a
  `pathOrder -> path` dict; collect `mediaType=proxy` AND
  `target=web` entries similarly. Emit one rule per `pathOrder`
  present in both dicts. Drop unpaired orders silently — an
  orphan hires root (no matching proxy) is operationally identical
  to "we can't serve those clips locally", and we'd rather skip
  the rule than fabricate one.
- Resolution: linear scan of rules, first `startswith(hires_root + "/")`
  wins. Linear is fine — CatDV deployments rarely have more than a
  handful of media-store paths.

**Out of scope (explicit non-goals).**

- *Automatic detection that we're "on the CatDV host."* The deploy
  artifact selects `PROXY_SOURCE` explicitly. We don't probe whether
  `/Volumes/ARECA/CatDV_Proxy` is reachable before choosing the
  resolver — that's a deploy-time concern, not a runtime one.
- *Cache eviction of any pre-existing `data/cache/proxies/`
  contents on the on-host deploy.* They're stale once we stop
  writing to that directory; cleanup is a one-time manual `rm` if
  the operator cares.
- *Falling back to REST when a proxy is missing on disk.* Explicitly
  rejected — see "Why / Failure mode is loud" above.

**Cache-state UI invariant in host-local mode.**

The `proxy_cache` table is the source of truth for "have we
downloaded a copy of this proxy?" In `PROXY_SOURCE=filesystem` mode
no rows are ever written there, which would naively render every
clip's media-local glyph as `absent` and leave the "Cache locally"
and "Evict local" controls live (and useless). The deploy-side
truth is the opposite: every clip the catalog exposes is already on
the host's disk via the media-store mount, and the user has no
business "caching" or "evicting" anything.

The resolver Protocol therefore carries an `is_host_local: bool`
capability flag (False on `RestProxyResolver`, True on
`FilesystemProxyResolver`). The CacheInspector and clip-list filter
resolver branch on it: in host-local mode the media-local
`LayerStatus` is synthesised as `present=True, evictable=False,
location="host:filesystem"` without reading `proxy_cache`; the
`cache=local` filter contributes nothing and `cache=none` returns
the empty set. Templates hide the controls entirely (per "hide vs
disable" we chose hide — a disabled-with-tooltip variant was
considered and rejected as visual clutter, since the controls
genuinely do not apply, not just "not right now"). The cache page
itself stays unmodified — it lists `proxy_cache` rows, which is
correct: there aren't any in this mode, and "empty cache" is the
accurate state to show.

The chosen seam is the resolver capability rather than a global
`settings.proxy_source` check because the capability travels with
the object that has the most authoritative view of what "having a
proxy locally" means for a given clip — a future resolver that, say,
mirrors proxies into a per-tenant FUSE mount would also set
`is_host_local=True` and inherit the same UI behaviour without
touching the inspector or filter code.

## 2026-05-22 — Offline fallback: auto-degrade + manual reconnect

**Context:** The annotator crashed on startup without VPN and raised 5xx
on every read when CatDV went down mid-session. Users wanted to keep
working from the local cache while disconnected — list, open, scrub
clips that were already cached — without losing in-flight writes.

**Alternatives:** (a) New `CacheOnlyArchiveAdapter` wrapper class —
heavier, doubles the read-API test surface. (b) Strictly automatic
fallback driven by `ConnectionMonitor` only — no env override,
operators couldn't boot without VPN being up at startup. (c) Auto-
degrade inside the existing `CatdvArchiveAdapter` via an injected
`is_online_provider` callable, plus a `CATDV_OFFLINE` env override and
a user-triggered reconnect from a topbar chip.

**Choice:** (c). The 2026-05-19 abstraction already had cache-first
reads, `WriteQueue`, and `SyncEngine`; this finished the loop with the
smallest surface area. The connection state machine has exactly three
external states — `online`, `offline` (auto-degraded, reconnect via
chip), `forced_offline` (env flag, reconnect by restart). The monitor
halts its probe loop after a single failure rather than retrying
forever; the user reconnects on demand via `POST /api/connection/retry`.

**Why:** Existing tests keep passing — `is_online_provider` defaults to
`None` which the adapter treats as "always online", and `forced_offline`
defaults to `False` on the monitor. Writes get the existing queue
behavior for free: `apply_changes` raises `RetryableError` when offline,
which is exactly what `SyncEngine` already retries on. Auth failure at
startup is treated as offline rather than fatal, matching the spirit of
"the app should be usable without CatDV". Two adapter-level deviations
from the original plan are documented inline: the column is
`canonical_json` (not `blob_json`) so the `LIKE` for free-text search
uses `json_extract(canonical_json, '$.notes.notes')` to avoid false
positives on JSON-key substrings; and `CatdvClient.__aenter__` is lazy
about auth, so we call `client.login()` explicitly at boot to detect
unreachable/unauthorized servers cleanly.

## 2026-05-23 — Gemini Live clip assistant: browser-direct + Developer API

**Context:** Add a Czech voice assistant to the clip-detail view that
sees the current frame plus all annotation context and can ground
location / historical questions via Google Search. Spec:
`docs/specs/2026-05-23-gemini-live-clip-assistant-design.md`.

**Alternatives:** (a) Backend WSS bridge — browser ↔ FastAPI ↔ Vertex
AI Live — reusing the existing service-account credentials. (b) Vertex
AI Live opened browser-direct, using a 1-hour OAuth bearer minted from
the service account. (c) Gemini Developer API
(`generativelanguage.googleapis.com`) opened browser-direct, using
`authTokens.create` to mint a single-use ephemeral token with
`liveConnectConstraints` baked in.

**Choice:** (c). Audio flows browser ↔ Google directly; the FastAPI
process is only used to mint the token, assemble the system instruction
+ clip-context setup payload from the existing prompt-management
system, persist the post-session transcript, and run a non-Live
`generateContent` to produce a Czech summary stored alongside it in a
new `live_sessions` table. The assistant never writes to draft or
published annotations — its output lives in a read-only *History* tab
on the clip page.

**Why:** Option (a) was tried as a PoC and failed — the extra hop and
re-encoding scrambled the audio enough that Gemini could not understand
Czech speech. The user's instruction was explicit: implement direct
browser communication. Between (b) and (c), the Developer API's
`authTokens.create` is purpose-built for browser-direct Live — tokens
are single-use, short-lived (≤30 min), and bound to a specific model /
voice / tools / system-instruction so a leaked token can only open one
specific kind of session. Vertex's OAuth bearer is broader-scoped (full
Vertex AI for ~1 h) and has no equivalent constraint mechanism. The
existing Vertex usage (batch annotation flow) is unaffected; both
surfaces bill the same GCP project. A small one-shot `gcloud` script
in `deploy/enable-gemini-live.sh` enables the Generative Language API
and mints a project-scoped API key.

## 2026-05-23 — Offline mode: keep Annotate available when proxy is cached; marker nav follows active scope

**Context.** Two related clip-detail bugs surfaced after the
offline-mode rollout (PRs leading up to `b21c30f`): (1) the Annotate
dropdown disappeared in offline / degraded mode even when the clip's
proxy was already cached locally and Gemini was reachable; (2) the
prev/next-marker transport buttons always navigated the published
marker list, even when the user had switched the right-aside scope
to Draft.

**Alternatives & choices.**

1. *Annotate visibility offline.* The offline-fallback plan
   (`docs/plans/2026-05-22-offline-fallback.md` step 6) lumped
   Annotate together with "Cache locally" and "Refresh from CatDV"
   under a single `{% if mode == "online" %}` gate. We considered
   leaving it as-is (conservative) vs. gating on the real
   precondition — proxy cached locally. We chose the latter:
   `{% if mode == "online" or (clip.cache and clip.cache.media_local.present) %}`
   in `clip_detail.html`.

2. *Scope-aware prev/next.* Player only knew about
   `clip.markers` (published). Options: (a) drive a separate Alpine
   component for draft markers, (b) thread draft markers through the
   existing player as a second list and pick by `scope`. Chose (b):
   `player()` now takes a fourth `draftMarkers` arg, exposes an
   `activeMarkers()` method that returns `scope === "draft" ? draftMarkers : markers`,
   and the transport buttons / arrow-key handler / `_jumpMarker`
   all read through it. The `:disabled` binding in the template
   becomes `:disabled="!activeMarkers().length"`.

**Why.**

For (1): the design spec's own acceptance criteria
(`docs/specs/2026-05-19-archive-abstraction-and-offline-mode-design.md`
§"Acceptance") lists "Go offline → annotate (cached) → reconnect →
sync" as a target. The annotate pipeline (`services/annotator.py`)
resolves the proxy through `LocalCacheOnlyResolver`, uploads to
Gemini/GCS directly, reads clip metadata from the stale-cache adapter,
and stores results in local SQLite. Apply-to-CatDV is a separate
later step that already queues through `pending_operations`. So
hiding Annotate when offline was strictly more restrictive than the
spec required and broke the documented offline workflow.

For (2): keeping a single `markers` array meant the user could see
draft marker ranges on the timeline but the "next/prev marker"
controls would silently jump to the wrong list. A method (not a
getter) was chosen because the player object is merged into the
final Alpine scope via `Object.assign(player(...), clipAnnotate(...), {scope, tab})`
— a plain function-valued property survives `Object.assign` and
Alpine's reactive wrapping more predictably than an accessor
descriptor on the merged target.

---

## 2026-05-23 — Gemini Live clip assistant: browser-direct WSS, separate view-model

**Context.** Adding a Czech voice assistant to the clip-detail page
(spec `docs/specs/2026-05-23-gemini-live-clip-assistant-design.md`;
plan `docs/plans/2026-05-23-gemini-live-clip-assistant.md`). Two
calls during implementation deserve their own note: (1) the audio
path is browser→Google→browser direct, not through our backend;
(2) the live context-text builder uses a purpose-built view-model
shape, not the existing `clip_detail()` / `_build_draft_for_clip`
view-models passed to `clip_detail.html`.

**Alternatives & choices.**

1. *Audio routing.* The plan's spec §3.2 documents a prior PoC that
   bridged Live audio through FastAPI; that path added enough latency
   and re-encoding that Gemini misunderstood the Czech speech. The
   options reduced to (a) keep the bridge and tune the codec, or
   (b) mint a single-use ephemeral token server-side via
   `authTokens.create` and have the browser open the WSS directly.
   We chose (b). Backend's role is now exactly three HTTP calls
   (`POST authTokens.create`, `POST sessions/{id}/transcript`,
   `POST sessions/{id}/summarize` via non-Live `generateContent`)
   and no socket. The browser owns the WSS lifecycle, mic capture,
   playback, frame extraction, and the inactivity timer. The result:
   the FastAPI process never sees a PCM byte.

2. *View-model shape for `build_context_text`.* The plan's tests for
   `services/live_context.py` assume `fields` is a `dict[ident, value]`
   and markers carry `in_smpte` / `out_smpte`. The existing clip
   view-model (`ui/view_models.clip_detail`) returns `fields` as a
   list of `{identifier, name, value}` dicts and markers without
   smpte (only `in_secs`/`out_secs`). Options: (a) bend the builder
   to accept the existing list+secs shape, (b) keep the builder's
   simple shape and convert at the loader boundary. We chose (b):
   `routes/pages.py` exposes `_build_clip_view_model_for_live` and
   `_build_draft_view_model_for_live` that produce dict-shaped
   fields and smpte-stamped markers, which `routes/live.py` calls
   via `load_clip_for_live` / `load_draft_for_live`. This keeps the
   pure Czech-text builder readable (no defensive type-sniffing) and
   localises the format choice to one helper per side.

**Why.**

For (1): the spec's "audio never traverses backend" is the
single hardest-locked decision in the design — losing it would
re-introduce the PoC's latency failure. The route surface is
deliberately tiny so it's hard to accidentally drift toward a
WebSocket route on our side; the only Gemini HTTP calls inside
the process are the token mint and the post-session summarise.

For (2): the existing clip view-model is shaped for HTML
rendering (pre-stringified field values, marker objects geared to
the timeline). The Live context block is a free-text Czech blob
the model reads — it wants raw values to format itself
(`pragafilm.rok.natočení: 1928, 1929` not `pragafilm.rok.natočení: 1928, 1929`
re-fixed-up by a string view), and human-readable smpte timestamps
for the markers because the model never reads `in_secs=12.5`. Trying
to share one VM would have meant the live builder doing inverse
operations (parsing the existing string values back into lists, then
re-formatting; converting `_secs` back to smpte). Two narrow
purpose-built helpers cost less than that.

## 2026-05-23 — Tier 1 tooling: ruff format, basedpyright with baseline, pre-commit

**Context.** A senior reviewer flagged the backend as feeling chaotic
despite real layering (`archive/` → `repositories/` → `services/` → `routes/`).
The signals were 89 outstanding ruff errors, no type checker, no
pre-commit hook, scattered `# noqa: E402` workarounds in `main.py`,
and no enforcement that the layer rules are honored. Goal: make the
codebase orientable for a new human developer and stop the bleeding
at the keyboard, not in review.

**Alternatives.**

1. *Type checker.* mypy (`--strict`) vs basedpyright vs pyright.
   mypy is the default but slow on this 11.6k-line tree, and slow
   pre-commit hooks get skipped. basedpyright is a maintained fork
   of pyright that's pip-installable (no node), runs in seconds,
   and supports a `--baseline` file out of the box.
2. *Type-checker rollout strategy.* Either drive the codebase to
   zero errors today, or capture a baseline and ratchet later. The
   first option requires touching ~50 files, much of it in tests
   that pre-date this initiative. The second option lets us turn
   the hook on now without blocking commits on legacy noise.
3. *ASYNC240 lint rule.* Either fix every blocking `Path.stat()`
   / `Path.exists()` call with `asyncio.to_thread`, or stop linting
   for them. We use neither anyio nor trio (the rule's premise),
   and the calls in question are sub-ms local-SSD stats — wrapping
   them in `to_thread` costs more than it saves and adds visual
   clutter to every cache/proxy resolver. The one genuinely
   blocking call (writing a multi-hundred-MB proxy stream in
   `catdv_client._stream_to_file`) is fixed properly with
   `to_thread` per chunk.

**Choice.**

- `ruff format` enabled (replaces black + isort; ruff was already in
  the dev deps with sensible lint config).
- `basedpyright` added with `typeCheckingMode = "basic"` and a
  baseline at `.basedpyright/baseline.json` (237 pre-existing errors
  snapshotted; `0 errors` now means "no new ones"). Baseline refresh
  is a one-line command documented inline in `.pre-commit-config.yaml`.
- `ASYNC240` disabled globally in `pyproject.toml` with an inline
  comment explaining why. The single legitimate blocking-I/O case
  in `_stream_to_file` is fixed with `asyncio.to_thread` per chunk
  and carries a `# noqa: ASYNC230` plus a code comment.
- `B017` (`pytest.raises(Exception)`) added to per-file ignores for
  `tests/` — that pattern is intentional when the contract under
  test only promises "raises *something*."
- `pre-commit` wires ruff check (with `--fix`), ruff format, and
  basedpyright. Hook versions pinned to match the project's local
  ruff (`v0.15.13`) — version drift between local and pre-commit
  ruff caused `Unknown rule selector` errors when the hook used an
  older release.
- `main.py` rewritten: all router imports moved to the top, router
  registration extracted into `register_routers(app)`. Removes the
  nine `# noqa: E402` workarounds the old shape required, and
  gives a new contributor one place to see the full route surface.

**Why.**

The architecture was fine; the chaos was the *absence of
enforcement*. Once pre-commit refuses to land unformatted code or
new type errors, the layers we already have stay legible. Picking
basedpyright + a baseline file specifically solves the "237 latent
errors block every commit" trap that kills mypy adoption on
existing codebases. Disabling `ASYNC240` is a deliberate scope cut:
the rule was selected aspirationally but the codebase never
followed it; making the rule's intent visible at the disable site
is better than littering the repo with `# noqa`. The remaining
Tier 2/3 items (import-linter for layer enforcement, `CONTEXT.md`
glossary, `ARCHITECTURE.md` map, splitting the 663-line
`routes/pages.py`) are intentionally out of scope here — those are
orientation work, this is hygiene work.

## 2026-05-23 — Typed `get_ctx` accessor (PR E of arch plan)

**Context.** Every route reached into `request.app.state.ctx` to grab
the live `AppContext`. Starlette types `app.state` as the catch-all
`State` class, so basedpyright saw every `ctx.archive`, `ctx.db`,
`ctx.workspace_manager`, etc. as an `Any` attribute access. The
plan's hypothesis was that the bulk of the 237 baseline errors were
this `Any`-poisoning and that one typed accessor would knock a chunk
of them out.

**Alternatives.**

- `Annotated[AppContext, Depends(get_ctx)]` parameter on every
  handler. Cleaner, idiomatic FastAPI, but a much larger diff and
  the per-handler signature noise didn't seem worth it for a pure
  type-ergonomics PR — leave for a follow-up if useful.
- Cast at every call site (`cast(AppContext, request.app.state.ctx)`).
  Same error suppression, ~14 type-ignores instead of 1, no central
  place to evolve the pattern.
- Leave it alone and target other categories first. Tempting after
  seeing the result below, but the chokepoint is still worth having
  — the rest of the codebase no longer needs a `# type: ignore` to
  reach the context.

**Choice.** A single helper in `backend/app/deps.py`:

```python
def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx  # type: ignore[no-any-return]
```

Routes call `ctx = get_ctx(request)` inline. The lone
`# type: ignore` is contained to one file.

**Why / what we learned.** The baseline did *not* shrink — it grew
from 237 to 273 errors (+36). The reason: most `AppContext` fields
are typed `T | None` because they're only wired during a successful
online boot (`sync_engine`, `workspace_manager`, `connection_monitor`,
`media_prefetcher`, `cache_actions`, etc.). When ctx was `Any`,
`ctx.sync_engine.drain_once()` was silently `Any.Any()`; now it's a
real `reportOptionalMemberAccess`. Breakdown of the +36:
`reportOptionalMemberAccess` 9 → 40 (+31), `reportArgumentType`
74 → 78, plus a handful of misc. All real, all previously hidden.

This is still the right trade. The whole point of the type system is
to make these latent None-derefs visible — and route handlers *do*
need to handle the missing-service case (the `main.py` health endpoint
already uses `getattr(..., None)` exactly for this reason). The
follow-up is to either narrow with explicit `if ctx.sync_engine is
None: raise HTTPException(503, ...)` guards (some routes already do
this) or to split `AppContext` into "always-present core" and
"optional services" types. That's a separate PR; this one's job was
to expose the surface area.
