# Draft review edit/accept UX — Save/Cancel editing, applied items leave Draft, recoverable deletes

**Date:** 2026-06-04
**Status:** Implemented

Origin: user-reported UX problems in the clip-detail Draft review panel
(built by `docs/specs/2026-06-02-draft-review-redesign-design.md`).

## Problem

Four concrete problems in the Draft review panel (`_anno_draft.html` +
`review.js` + `player.js`):

1. **Editing has no Save/Cancel.** Every `@change` on an edit input fires a
   POST to `/api/review/items/{id}/decision` that also flips the item to
   `accepted`. The actions row keeps showing **✎ Edit / Done** + **Delete**
   while editing — a destructive button sitting next to a half-finished edit,
   and no way to back out of a bad edit.

2. **Accepted edits sometimes don't get written through.** `_persistMarker`
   (`player.js`) fires its decision fetch fire-and-forget; it is *not*
   tracked in `reviewMixin._inflight`. `applyDraft` (`review.js`) only awaits
   `_inflight` before POSTing `/apply`, so a marker edit can still be in
   flight when the server resolves accepted items — the apply reads the
   stale/unedited value, or skips an item whose decision row is still
   `pending`.

3. **Applied items linger in Draft.** Apply marks items `applied_at` in the
   DB, but `draft_review_arrays` (`ui/view_models.py`) only excludes
   `rejected` items. After Accept & apply all → refresh, the applied items
   re-render as if they were still un-reviewed proposals.

4. **Deleted proposals are unrecoverable.** Delete sets decision `rejected`
   and the item is filtered out of every view. The decision endpoint already
   accepts `pending`, but no UI path exists to send it.

## Goals

- Edit mode is an explicit **Save / Cancel** transaction; Delete is not shown
  while editing. Nothing persists until Save.
- Timeline drag and ←/→ nudge of a draft marker are part of the same buffered
  edit: previewed live, persisted only on Save, snapped back on Cancel.
- Every decision write is awaited before Apply enqueues — the write-through
  race is structurally gone.
- After a successful apply, applied items leave the Draft panel; the panel
  says they're syncing to CatDV instead of re-listing them.
- Deleting a proposal is recoverable: an **Undo** toast immediately after,
  and an always-visible **Deleted (n)** section per tab with **Restore** buttons
  any time later.

## Non-goals

- No change to the Published panel, the sync engine, or the write queue.
- No per-clip surfacing of upstream sync failures (the sync drawer already
  owns that).
- No change to the bulk/yolo apply path (`/api/review/apply-batch`).
- Studio output cards (which share `build_draft_view`) keep their behavior;
  only the clip-detail Draft panel UI changes.

## Design

### 1. Save/Cancel edit mode (frontend)

State machine per item (in the composed clip-detail `x-data`):

- **Open Edit** (`startEdit(item)`): deep-copy the item into
  `_editSnapshot`; set `editingItemId`. Inputs keep `x-model` binding to the
  *live* item so the card text and the timeline bar preview edits live —
  all `@change="…persist…"` handlers are removed from `_anno_draft.html`.
- **Timeline drag / nudge** (`_endMarkerDrag`, `nudgeMarker` in `player.js`):
  stop calling `_persistMarker`; they just mutate the live item, which is
  covered by the snapshot.
- **Save** (`saveEdit()`): one tracked persist — decision `accepted` +
  `edited_value`. Markers send the full
  `{name, category, description, in:{secs}, out:{secs}?, color?}` shape
  (the backend `COALESCE` replaces `edited_value` wholesale). Fields send
  `value`, notes send `text`. Sets `status = "accepted"`, clears
  `editingItemId` + snapshot.
- **Cancel** (`cancelEdit()`): copy the snapshot back into the live item
  (timeline bar snaps back), clear `editingItemId` + snapshot. No POST.
- **Switching items**: opening Edit on another item while one is open
  auto-saves the current edit first (silently discarding edits would be
  worse; drafts are low-stakes).
- **Apply while editing**: `acceptApplyAll` auto-saves any open edit before
  accepting and applying — otherwise a buffered edit would be silently
  dropped, recreating the very write-through bug this spec fixes.

Actions row in `_anno_draft.html` becomes state-dependent:

- Not editing: **✎ Edit** · **Delete** (as today).
- Editing this item: **Save** (primary) · **Cancel** (ghost). No Delete.

`_persistMarker` is removed from `player.js`; the marker-payload shaping
moves into `saveEdit()` in `review.js` (its only remaining caller).

### 2. Write-through race fix

All decision writes go through `reviewMixin._persist`, which already tracks
its promise in `_inflight`; `applyDraft` already awaits
`Promise.allSettled([...this._inflight])`. With drag/nudge no longer
persisting and `saveEdit` routing through `_persist`, every write that can
precede an Apply is awaited. No backend change needed.

### 3. Applied items leave Draft (backend + frontend)

- `build_draft_view` (`services/draft_view.py`) passes `applied_at` through
  on each marker / field / note item dict.
- `draft_review_arrays` (`ui/view_models.py`) excludes items with
  `applied_at` set from the main arrays (in addition to `rejected`), and
  returns `applied_count` (number of applied, non-rejected items).
- `clip_detail.html` seeds `appliedCount` into the Alpine state;
  `refreshDraft` updates it from the draft-data JSON.
- Draft panel empty state: when `totalCount() === 0 && appliedCount > 0`,
  show "N proposal(s) applied — syncing to CatDV. They'll appear under
  Published once synced." instead of "No proposals to review."

### 4. Recoverable deletes (backend + frontend)

- `draft_review_arrays` adds a `deleted` bucket
  (`{markers: [], fields: [], notes: []}`, same item shapes) containing
  `rejected`, non-applied items.
- `clip_detail.html` seeds `draftDeleted`; `refreshDraft` updates it.
- **Delete** moves the item from the live array into `draftDeleted.<kind>`
  locally, persists `rejected` (tracked), and pushes a toast
  "Proposal deleted." with an **Undo** action.
- **Undo / Restore** (`restore(item)`): persists decision `pending`
  (endpoint already supports it), moves the item back from
  `draftDeleted.<kind>` into the live array, status `proposed`.
- Each tab renders an always-visible **Deleted (n)** section under the live cards
  (hidden when empty): muted one-line rows (name / identifier / text
  excerpt) + a **Restore** button.
- `toast.js` gains an optional `action: {label, fn}` option on `push`;
  the action button renders next to the message, runs `fn`, and dismisses
  the toast. (Action `fn`s are stored on the item, invoked via the store —
  the current innerHTML rendering can't serialize closures.)

## Data / API changes

- `GET /api/review/clips/{id}/draft-data` and the page-render
  `draft_arrays` gain `applied_count: int` and
  `deleted: {markers, fields, notes}`. Existing keys unchanged.
- No new endpoints; restore reuses `POST /api/review/items/{id}/decision`
  with `{"decision": "pending"}`.
- No schema changes.

## Testing

TDD throughout (per CLAUDE.md):

- **Unit** (`tests/unit/test_draft_review_arrays.py`): applied items
  excluded from main arrays; `applied_count` correct; rejected items land
  in `deleted` and not in main arrays; applied+rejected items appear in
  neither.
- **Unit** (`tests/unit/` draft_view): `applied_at` passes through
  `build_draft_view` item dicts.
- **Integration** (review routes): decision round-trip
  `rejected → pending` un-hides the item in draft-data; after
  apply, draft-data main arrays are empty and `applied_count > 0`.
- Existing guard tests (`test_no_x_data_stack`, htmx/alpine lifecycle,
  templates-shared) must stay green.

## Manual acceptance flows

1. **Edit → Save.** Open a clip with draft proposals
   (`/clips/{id}?review=1&scope=draft`), Markers tab. Click **✎ Edit** on a
   marker: the actions row shows **Save / Cancel** only (no Delete). Change
   the name and drag the highlighted bar on the timeline. Click **Save**.
   Reload the page: the new name and in/out persist.
2. **Edit → Cancel.** Edit the same marker, change name + drag the bar,
   click **Cancel**: the card text reverts *and* the timeline bar snaps back
   to its pre-edit position. Reload: unchanged values.
3. **Accept while editing (race + auto-save).** Edit a marker's
   description, and *without clicking Save* click **✓ Accept & apply all**
   directly. The open edit is auto-saved, then applied. After the success
   toast, check the marker in CatDV (or the pending op in the sync drawer):
   it carries the edited description.
4. **Post-apply empty state.** After flow 3, the Draft panel shows
   "N proposals applied — syncing to CatDV…", count 0, no lingering cards.
   Once the sync engine drains, the items appear under **Published**.
5. **Delete → Undo.** Delete a proposal: it leaves the list and a toast
   "Proposal deleted. **Undo**" appears. Click Undo: the proposal returns
   to the list as Proposed. Reload: still present.
6. **Delete → Restore later.** Delete a proposal, dismiss the toast. A
   **Deleted (1)** section appears at the bottom of the tab.
   Reload the page: the section is still there. Expand it, click
   **Restore**: the proposal returns to the live list as Proposed and the
   Deleted section empties/hides.
7. **Regression: clip walk + accept all.** With ≥2 clips in the review
   queue, ‹ › still walks clips; **✓ Accept & apply all** with no edits
   open still applies everything listed and flow-4's empty state shows.
