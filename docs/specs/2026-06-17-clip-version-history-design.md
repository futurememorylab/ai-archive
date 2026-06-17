# Clip version history, publish snapshots, and a unified status model

**Date:** 2026-06-17
**Status:** Design — approved for planning
**Author:** brainstorming session (adam + Claude)

## Problem

Three operator pains with how "drafts" work today:

1. **Re-runs silently orphan work.** Re-annotating a clip inserts a new
   `annotation`, but the old annotation's `review_items` linger in SQLite and
   vanish from the UI — `_build_draft_for_clip` only renders `annotations[0]`.
   There is no version picker, no compare, no carry-forward.
2. **No history / rollback.** Once a change is applied to CatDV, there is no
   durable record of what the clip looked like before, what the AI proposed
   vs. what a human chose, or who published it — and no way to revert.
3. **Status is confusing.** At least four overlapping vocabularies describe
   "where is this change": `review_items.decision`, `applied_at` vs.
   `synced_at`, the `pending_operations` enum, and job-item statuses. There is
   no single headline a user can trust at a glance.

Explicitly **out of scope**: two-way reconciliation from CatDV. Edits made
directly in CatDV's own web client do **not** need to flow back into our
drafts. This means our SQLite can remain the canonical store for draft and
version state; CatDV stays the publish target.

## Decisions (from the brainstorming session)

- **A version is a publish (a commit).** History is the list of published
  states for a clip — like git commits — not the list of raw AI runs.
- **Re-run replaces the working draft.** The last *published* version is
  always restorable; only uncommitted scratch is discarded (behind a confirm
  dialog when an unpublished draft exists).
- **History lives canonically in our SQLite** (`clip_versions`), durable and
  shared via Litestream (ADR 0066). CatDV has no version-history primitive, so
  it keeps holding only the current live state.
- **CatDV carries a light provenance breadcrumb.** Each publish writes one
  user field, `pragafilm.anno_version`, so CatDV-native users can see a clip
  was AI-annotated and which version is live. The real history stays local.
- **The headline status is publish-state-centric:** `Live (vN)` /
  `Draft – unpublished` / `Publishing…` / `Failed` (+ `Conflict`), one
  vocabulary across the topbar chip, clips list, and clip detail.
- **Implementation = a snapshot layer on top of the existing machinery**
  (Approach A). `review_items` stays the working draft; the durable write
  queue + `SyncEngine` (ADRs 0091–0095) are untouched. We add one table, one
  service, one status-derivation function, and UI wiring.

## Architecture

The working draft is unchanged: `annotations` → `review_items` (per-clip
proposals + human edits + accept/reject), driven by the annotator/studio. We
add a **commit layer** on top.

```
 AI run ──> annotation ──> review_items        (the WORKING DRAFT — scratch)
                              │  accept/edit
                              ▼
                          PUBLISH  ─────────────┐
                              │                 │
                  materialize full snapshot     │ enqueue ops (existing)
                              ▼                 ▼
                       clip_versions      pending_operations ──> SyncEngine ──> CatDV PUT
                       (immutable commit)        │                                  │
                       publish_state=            │ origin_clip_version_id           │ ok
                         'publishing'            └──────────────────────────────────┘
                              ▲                                                      │
                              └────────── flip to 'live' + supersede prior ──────────┘
```

### Component boundaries

- **`ClipVersionsRepo`** — CRUD for `clip_versions`. Leaf repository (no
  service imports). Batched reads for the status badge (`chunked_in_clause`).
- **`PublishService`** (new, or a focused module under `services/`) — owns
  *materialize snapshot → insert version → enqueue ops → notify*. The current
  `_resolve_and_enqueue_clip` in `routes/review.py` becomes a thin caller.
- **`clip_publish_status(...)`** — pure derivation: `(has_draft, newest
  version state) → headline enum`. One function, three consumers.
- **`SyncEngine._handle_result`** — extended to flip the originating
  `clip_versions` row's `publish_state` on `ok` / `conflict` / `failed`.

## Data model

### New table `clip_versions`

```sql
CREATE TABLE clip_versions (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  provider_id        TEXT    NOT NULL DEFAULT 'catdv',
  catdv_clip_id      INTEGER NOT NULL,
  version_num        INTEGER NOT NULL,          -- per-clip sequential (#1, #2…)
  parent_version_id  INTEGER REFERENCES clip_versions(id),
  snapshot           TEXT    NOT NULL,          -- JSON: {markers, fields, notes, bigNotes}
  diff               TEXT,                      -- JSON: delta vs parent (added/changed/removed)
  origin             TEXT    NOT NULL,          -- 'publish' | 'restore'
  model              TEXT,                      -- denormalized provenance
  prompt_version_id  INTEGER,
  annotation_id      INTEGER REFERENCES annotations(id),
  author             TEXT,                      -- IAP identity (ADR 0084/0085); '—' for backfill
  publish_state      TEXT    NOT NULL,          -- 'publishing'|'live'|'superseded'|'failed'|'conflict'
  expected_etag      TEXT,
  failed_reason      TEXT,
  synced_at          TEXT,                      -- confirmed live on CatDV
  created_at         TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX ix_clip_versions_clip ON clip_versions(provider_id, catdv_clip_id, version_num DESC);
-- At most one 'live' per clip is an invariant enforced in code (supersede on flip),
-- not a partial unique index, to keep the conflict/failed transitions simple.
```

Rows are **immutable** except for the `publish_state` / `synced_at` /
`failed_reason` transition the `SyncEngine` performs. Restore never mutates a
row — it publishes an old snapshot *forward* as a new row.

### `snapshot` shape

The full committed state (not a delta), so Restore can re-PUT it verbatim:

```json
{
  "markers": [{"name": "...", "category": "...", "description": "...",
               "in_secs": 12.0, "out_secs": 18.5, "color": "..."}],
  "fields":  {"pragafilm.genre": "thriller", "pragafilm.summary": "..."},
  "notes":   "AI summary text…",
  "bigNotes": null
}
```

### Extension to `pending_operations`

Add `origin_clip_version_id INTEGER REFERENCES clip_versions(id)` (nullable),
mirroring the existing `origin_annotation_id` / `origin_review_item_ids`
columns. This is the hook `SyncEngine._handle_result` uses to flip the
version's `publish_state`.

### Unchanged

`review_items.applied_at` / `synced_at` stay (the engine still stamps
`synced_at`). Only the *UI headline* stops reading them directly — it reads
the derived status instead. No data migration of those columns.

## Status model

A **fixed enum** declared in `backend/app/enums/registry.py` with
`editable=False`, mirrored by a `Literal` in `models/` and pinned by a guard
test (per the enumeration discipline in CLAUDE.md):

```python
ClipPublishState = Literal["none", "draft", "publishing", "live", "failed", "conflict"]
```

```python
"clip_publish_state": EnumSpec(
    key="clip_publish_state",
    name="Clip publish state",
    description="Headline status of a clip's annotation work vs CatDV.",
    editable=False,
    values=(EnumValueSpec("none"), EnumValueSpec("draft"),
            EnumValueSpec("publishing"), EnumValueSpec("live"),
            EnumValueSpec("failed"), EnumValueSpec("conflict")),
),
```

**Derivation** (`clip_publish_status`), with precedence
`failed/conflict > publishing > draft > live > none`:

| Signal | Source | Headline |
|---|---|---|
| newest version `publish_state ∈ {failed, conflict}` | `clip_versions` | `Failed` / `Conflict` |
| newest version `publish_state = publishing` | `clip_versions` | `Publishing…` |
| un-applied `review_items` exist for the clip | `list_pending_clips` (existing) | `Draft – unpublished` |
| a `live` version exists | `clip_versions` | `Live (vN)` |
| otherwise | — | `none` |

The two inputs are both cheap and already-computed: "draft exists" is exactly
what `ReviewItemsRepo.list_pending_clips` returns; the version state is one
indexed read per clip. The clips-list badge derives all rows in **one batched
query** (N+1 guard, ADR 0046) — never per-row.

Rendered everywhere via the existing `status_pill(label, state)` macro
(state → colour: `live`→ok, `publishing`→accent, `draft`→neutral,
`failed`/`conflict`→bad). JS reads labels from `window.APP_ENUMS.clip_publish_state`.

## Flows

### Publish (today's "Apply", wrapped)

`routes/review.py::apply_clip` → `PublishService.publish(clip_id)`:

1. Resolve the clip's accepted `review_items` (existing `list_by_clip(decision="accepted")`).
2. **Materialize the full snapshot**: start from the current `live` version's
   snapshot (or the clip's current CatDV/cached state if none) and lay the
   accepted markers (add), fields (set), notes/bigNotes (set) on top.
3. Insert the `clip_versions` row: `version_num = MAX+1`, `parent = current
   live id`, `snapshot`, `diff` vs parent, provenance (`model`,
   `prompt_version_id`, `annotation_id`, `author`), `publish_state='publishing'`,
   `origin='publish'`, `expected_etag = etag_from_snapshot(annotation.clip_snapshot)`.
4. Enqueue ops via the existing `write_queue.enqueue_apply_for_clip(...)`, now
   also passing `clip_version_id`. **Append one extra op**:
   `SetField("pragafilm.anno_version", "#N · {author} · {ts} · {model}")`
   — an ordinary `SetField` that `build_put_payload` already routes under
   `fields`.
5. Mark the resolved `review_items` applied (existing `mark_applied`); notify
   the engine.
6. `SyncEngine._handle_result` on `ok`: flip this version `publishing → live`,
   mark the prior `live` for the clip `→ superseded`, stamp `synced_at`
   (alongside the existing `review_items.mark_synced`). Headline goes
   **Publishing… → Live (vN)**. On `conflict` / `failed`: set the version's
   state accordingly and **leave the prior `live` untouched** (CatDV still has
   it); headline surfaces **Conflict** / **Failed**.

### Restore

History panel lists `clip_versions` newest-first. On a non-live version:

- **Restore** — load that version's `snapshot` into a fresh working draft and
  land the user in the normal Draft state to review/tweak, then Publish.
  Mechanics: clear the clip's un-applied `review_items`, then recreate
  `review_items` from the snapshot. Each recreated item references the restored
  version's stored `annotation_id` (satisfies the `review_items` CHECK
  constraint that exactly one of `annotation_id` / `studio_run_id` is set, and
  preserves provenance); if that annotation no longer exists, fall back to the
  clip's latest annotation. Publishing creates a **new** version
  (`origin='restore'`, `parent =` the restored version). History is never
  mutated. Headline: Restore → Draft → Publish → **Live (vN+1)**.
- **Restore & publish now** (secondary one-click) — same, but skips the review
  pause and publishes immediately, for the plain "just put v2 back" case.

### Re-run

Annotator/studio finalize, when a working draft already exists for the clip:
the new run's items become the working draft and prior **un-applied**
`review_items` for the clip are cleared (this also fixes today's orphaning).
The last published `clip_versions` row is untouched and restorable. The clip
page shows a confirm dialog (`ui.modal`) **only when an unpublished draft
exists**: "This replaces your current unpublished draft — last published #N
stays restorable."

## UI surfaces (all reuse — no new component vocabulary)

- **Clip detail** (`pages/clip_detail.html`): Draft/Published panels
  (`_anno_panels.html`) unchanged. Add a **History** dropdown via
  `ui.menu` / `ui.menu_item` + `popover()`; each row uses `status_pill` for
  its state and `ui.button` for Restore / Restore & publish. A single headline
  pill sits above the panels. The per-version **diff view** reuses the
  existing **Studio compare** diff styling (read `static/studio.js` + the
  compare partial first; extract a shared partial if needed rather than
  parallel-evolve).
- **Clips list** (`pages/_clips_row_cells.html`): the draft-count cell becomes
  the unified status badge (`status_pill`), fed by the batched derivation.
- **Topbar chip** (`_sync_chip.html` / `_sync_chip_inner.html`): re-point its
  counts at the unified status (publishing / failed). Popover + drawer
  retry/discard unchanged.

## Migration / backfill

- New migration `00NN_clip_versions.sql` (next free number — pick at
  implementation time; numbers collide across parallel branches). Latest on
  this branch is `0022`.
- Same migration adds `pending_operations.origin_clip_version_id`.
- **Backfill** (idempotent): synthesize a best-effort `live` **#1** for every
  clip that already has synced `review_items` — snapshot built from the last
  synced annotation's items, `author='—'`, no `expected_etag`,
  `origin='publish'`. So History isn't empty for already-published clips.
  (Lazy/empty-history rejected — it would look broken.)

## Scope notes for v1

- **"Draft" = pending proposals exist.** Editing an already-*published* clip
  without re-running (and accepting nothing new) won't flip the headline to
  Draft. Acceptable for v1 — Draft is about un-published proposals.
- **Conflict resolution stays as-is.** A `conflict` version surfaces in the
  headline; resolution reuses the existing sync drawer (ADRs 0091/0095)
  retry/discard. No new merge UI in v1.
- **Diff is best-effort provenance**, not a guarantee against CatDV-side
  drift (two-way sync is out of scope).

## Testing (TDD)

**Unit**
- Snapshot materialization: `live snapshot + accepted items → full state`
  (markers add, fields/notes set).
- Diff computation vs parent (added / changed / removed per kind).
- `clip_publish_status` precedence across every combination.
- `version_num` sequencing (MAX+1; per-clip; survives interleaved clips).
- Provenance op: extend `tests/unit/test_catdv_payload.py` to assert
  `pragafilm.anno_version` lands under `fields` with the expected string.

**Integration**
- Publish → `publishing`; SyncEngine `ok` → version `live` + prior
  `superseded` + `synced_at` stamped + `review_items.synced_at` stamped.
- SyncEngine `conflict` / `failed` → version state set, prior `live`
  untouched, headline reflects it.
- Restore-forward → new version with `origin='restore'`, correct `parent`,
  history unmutated.
- Re-run → working draft replaced, prior un-applied `review_items` cleared,
  last `clip_versions` row intact.
- Headline resolves correctly on clip detail, clips list, and topbar chip.

**Guards**
- N+1 query-count test on the clips-list status badge (10 vs 100 vs 1000
  clips → constant statements), using `assert_query_count`.
- Enum guard pinning `ClipPublishState` `Literal` ↔ registry values.

## Manual acceptance flows

1. **Publish creates a version and a CatDV breadcrumb.** On a clip with a
   fresh draft, accept items and Publish. Headline goes `Publishing…` →
   `Live v1`. The History dropdown shows `#1 · you · <time> · <model>`. In the
   CatDV web client (or a `get_clip` read), the clip carries
   `pragafilm.anno_version = "#1 · you · …"` and the applied notes/fields/markers.
2. **Re-run replaces the draft without losing the publish.** Re-annotate the
   same clip. The confirm dialog appears ("…last published #1 stays
   restorable"). Confirm. The Draft panel now shows the new run's proposals;
   History still lists `#1` as `live`. Headline = `Draft – unpublished`.
3. **Second publish supersedes the first.** Accept and Publish the re-run.
   Headline → `Live v2`. History shows `#2 live`, `#1 superseded`. CatDV
   `pragafilm.anno_version` now reads `#2 …`.
4. **Restore an old version forward.** From History, Restore `#1`. The working
   draft loads `#1`'s state; headline = `Draft – unpublished`. Publish →
   `Live v3` with `origin='restore'`, `parent = #1`. History shows three rows;
   `#1`/`#2` are `superseded`, `#3` is `live`.
5. **Restore & publish now (one-click).** From History on a clip with no
   pending draft, use Restore & publish on `#1`. Headline goes `Publishing…` →
   `Live v4` with no review pause.
6. **Failure is honest.** With CatDV offline, Publish a clip. Headline =
   `Publishing…`; the version stays `publishing` and the prior `live` is
   unchanged. After the retry ceiling, headline = `Failed`; the topbar chip
   shows the failure; the prior `live` version is still the live one and still
   restorable. Bring CatDV back, Retry from the sync drawer → `Live vN`.
7. **Status is consistent across surfaces.** For a clip mid-publish, the clips
   list badge, the clip-detail headline pill, and the topbar chip all read the
   same vocabulary (`Publishing…`), and all settle to `Live vN` together.
8. **Backfilled history isn't empty.** A clip that was published *before* this
   feature shipped shows a synthetic `#1` (`author='—'`) marked `live` in
   History; publishing new work advances to `#2`.
