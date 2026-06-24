# 0099. Clip version history: publish snapshots, local-canonical store, unified publish status

**Date:** 2026-06-17
**Status:** Superseded
**Lifespan:** Superseded

> **Synthesis note (2026-06-24):** Head of the clip-versions chain
> 0099 → 0100 → 0101; parts of this entry were superseded by **0100** (dropped
> the CatDV provenance field, switched versions by re-activation) and refined
> by **0101**. The current-state rule is **Invariant 19** in
> [`docs/architecture-invariants.md`](../architecture-invariants.md).

## Context

Operators hit three pains with how "drafts" worked (full design in
`docs/specs/2026-06-17-clip-version-history-design.md`):

1. **Re-runs silently orphaned work.** Re-annotating a clip inserted a new
   `annotation` but left the old annotation's `review_items` in SQLite,
   invisible in the UI (`_build_draft_for_clip` only renders the latest
   annotation). No version picker, no carry-forward.
2. **No history / rollback.** Once changes were applied to CatDV there was no
   durable record of the prior state, the AI-vs-human distinction, or who
   published — and no way to revert.
3. **Status was confusing.** Four overlapping vocabularies described "where is
   this change": `review_items.decision`, `applied_at` vs `synced_at`, the
   `pending_operations` enum, and job-item statuses.

Explicitly out of scope: two-way reconciliation from CatDV (edits made in
CatDV's own client do not need to flow back). That ruling is what lets our
SQLite remain the canonical store.

## Alternatives

- **Version unit = per AI run** (each annotate is a version). Rejected: a
  version should be a committed *result*, not a raw proposal set; run-level
  versioning multiplies noise and still needs a separate "what's published"
  notion.
- **Version unit = hybrid (track every run AND every publish).** Rejected as
  over-built for the stated pains; a single working draft + publish snapshots
  covers "don't lose work" + "history/rollback" without two parallel histories.
- **Store history in CatDV** (serialize snapshot JSON into a user field, or use
  a CatDV revisions API). Rejected: CatDV's REST surface exposes only *current*
  clip state (top-level props, user fields, markers) — no version-history
  primitive. Stuffing JSON blobs into a user field is size-limited,
  clobber-prone, seat-costly on every read/write, and unnecessary given
  two-way sync is out of scope.
- **Re-run merges into the working draft (3-way).** Rejected for v1 as the most
  work/most surface; the operator picked the simplest model.
- **Make `clip_versions` the spine** (fold `annotations`/`review_items` into a
  single versioned table). Rejected: a large, risky rewrite of the annotator,
  studio finalize, review route, sync engine, and every draft template that
  re-derives what the snapshot-layer approach gets for free.
- **Headline status = "work to review" or a two-part badge.** Rejected: the
  operator wanted one question answered everywhere — *is it live on CatDV?*

## Decision

- **A version is a publish (a commit).** Each publish writes one immutable
  `clip_versions` row: the full materialized `snapshot` (markers/fields/notes/
  bigNotes), a coarse `diff` vs the prior live, provenance (model, prompt
  version, author), and `publish_state`. History is the list of these rows.
- **Re-run replaces the working draft.** `_finalize_annotation` clears the
  clip's un-applied `review_items` before inserting the new run's items (the
  studio path is untouched). The last published version stays restorable, so
  nothing committed is lost.
- **Local-canonical store + a light CatDV breadcrumb.** `clip_versions` lives
  in our SQLite (durable + shared via Litestream, ADR 0066). CatDV keeps
  holding only current state, plus one provenance user field per publish,
  `pragafilm.anno_version = "#N · author · ts · model"`, written as an ordinary
  `SetField` op through the existing payload builder.
- **Approach A — a snapshot layer on top.** `review_items` stays the working
  draft. `PublishService` materializes the accepted draft into a `clip_versions`
  row (state `publishing`) and drives the ops through the **existing**
  `WriteQueue` / `SyncEngine`; a new `pending_operations.origin_clip_version_id`
  lets `SyncEngine._handle_result` flip the row `live` (superseding the prior
  live) on confirm, or `failed`/`conflict` on failure — leaving the prior live
  untouched. The retry-ceiling path also marks the version `failed`. ADRs
  0091–0098 (retry ceiling, append idempotency, conflict re-base) stay valid.
- **Restore is publish-forward, never a mutation.** `RestoreService` loads a
  chosen version's snapshot back into the working draft as fresh pending items;
  publishing it creates a new `origin='restore'` version. A one-click
  restore-and-publish skips the review pause.
- **One publish-state headline.** `clip_publish_state` is a fixed enum
  (`enums/registry.py`, `editable=False`, pinned to a `Literal` by a guard
  test). `resolve_publish_status(has_draft, version_state, version_num)` is the
  single derivation (precedence `failed/conflict > publishing > draft > live >
  none`), consumed by the clip-detail headline pill, the clips-list badge
  (batched via `newest_state_by_clip`, N+1-guarded), and the topbar sync chip
  (relabelled off the existing `count_actionable`, no new source of truth).
- **Idempotent backfill.** At boot, clips with synced `review_items` but no
  `clip_versions` row get a best-effort synthetic `live` v1 (author `—`), so
  History isn't empty for clips published before this shipped.

## Consequences

- Re-runs no longer orphan work; every published state is restorable; the
  AI-vs-human/author/diff record is durable in our DB.
- CatDV-native users see a clip was AI-annotated and which version is live via
  `pragafilm.anno_version`, without us depending on CatDV to store history.
- The proven write path is unchanged — the version flip is strictly additive in
  `SyncEngine._handle_result` (guarded on the repo being wired), so the full
  write-back/sync suite stayed green.
- "Draft" is detected as "un-applied `review_items` exist", so editing an
  already-published clip *without* re-running (and accepting nothing new) won't
  flip the headline to Draft — acceptable for v1 (Draft is about un-published
  proposals).
- Conflict resolution is unchanged: a `conflict` version surfaces in the
  headline; resolution reuses the existing sync drawer Retry (ADRs 0091/0095/
  0098). No new merge UI in v1.
- The `apply_clip` JSON contract changed `{queued}` → `{version_id}`;
  `review.js` was updated to treat a non-null `version_id` as "ops enqueued" so
  the post-apply sync poll + topbar refresh still fire.
- UI is all reuse (`ui.menu` / `ui.status_pill` / `ui.modal`, existing `.muted`
  styling) — the design-language guard stays green; no executing JS tests
  (ADR 0001), so the UI is pinned by server-side render/template-string guards.
- Manual acceptance flows (§ in the spec) require a running dev server + a human
  click-through and were deferred to a separate verification pass.
