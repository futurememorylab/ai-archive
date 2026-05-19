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
