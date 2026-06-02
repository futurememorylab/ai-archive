# Draft review redesign — readable accept/delete cards, 3-color timeline, clip-walk

**Date:** 2026-06-02
**Status:** Approved (design)

Origin: Claude Design handoff (`clip.html`) + the deferred "review walk" from
`docs/specs/2026-06-02-batches-new-batch-picker.md`. Builds on branch
`feat/batches-hub` (the Batches hub + picker), whose batch **Review →** is the
primary entry point.

## Problem

The clip-detail **Draft** review works but reads poorly: proposals render
through the shared `_anno_panels.html` (a "keep" checkbox + inline fields),
text is cramped, and the accept/edit affordances are unclear. The design
reworks the Draft scope into **readable proposal cards** with an explicit
Proposed/Accepted state, **Accept / Edit / Delete** per proposal, **Accept
all**, an **Apply (N)** action, a **3-color timeline** (proposed / accepted /
editing), and a **clip-by-clip ‹ › walk** across the review set so a reviewer
can move file-to-file without leaving the page.

Crucially, most of the machinery already exists and is reused as-is:

- **Scope toggle** (Published ↔ Draft) — `clip_detail.html`.
- **Draft-marker timeline drag-edit** — `player.js` (`startMarkerDrag`,
  `editingItemId`, `nudgeMarker`, and `_persistMarker`, which already POSTs the
  full marker to `/api/review/items/{id}/decision` as `accepted` + `edited_value`).
- **Clip-walk queue** — `reviewQueue` in `review.js` (a `sessionStorage`
  queue + `?review=1`, with `prev()`/`_next()`).
- **Decision + apply API** — `POST /api/review/items/{id}/decision`
  (accepted/rejected/pending + `edited_value`), `POST /api/review/clips/{id}/apply`
  (enqueues accepted items, returns the re-rendered draft partial on `HX-Request`).
- **Draft view-model** — `build_draft_view` already emits each marker / field /
  note with `item_id` + `decision`.

So this is mainly a **frontend redesign** of the Draft panel + review bar +
timeline colors, plus a tiny backend addition for the batch review queue.

## Goals

- Draft proposals render as **cards**: a Proposed/Accepted state chip, full
  readable text (marker name+description / field value / note text), and
  **Accept · ✎ Edit · Delete** actions. Markers / Fields / Notes tabs.
- **Review bar** (draft scope): **✓ Accept all** · accepted count **✓ N/M** ·
  **‹ Clip i/N ›** (walks the review set, preserving draft scope) · **Apply (N)**
  (apply + stay).
- **3-color timeline** in draft scope: blue = proposed, yellow = accepted,
  purple = editing; the published range row is hidden while reviewing.
- **Edit in place**: marker name/category/description in the card; in/out by
  dragging the highlighted bar on the timeline (existing player behavior) with
  a live in/out readout.
- **Delete = reject** (non-destructive): the proposal leaves the view and is
  excluded from Apply, but the `review_item` row survives.
- **Apply = apply + stay**: enqueue this clip's accepted proposals upstream and
  re-render in place; the reviewer advances with ‹ ›.
- The batch **Review →** seeds the walk with the batch's ordered pending clips.
- The **Published** panel is unchanged.

## Non-goals

- Changing the Published scope rendering, the player transport, or the
  marker-drag mechanics themselves (reused as-is).
- New apply/decision endpoints — the existing ones cover accept/reject/edit/apply.
- A standalone `/review` page — review stays on the clip-detail surface
  (`?review=1`), matching today's build.
- Multi-clip "apply all at once" — Apply is per-clip; the walk is manual via ‹ ›.
- Restyling the clips-list "Review selected" seeding (it already writes the
  `reviewQueue`; only the batch path is added).

## Design

### Draft data as Alpine state

The Draft panel becomes **Alpine-rendered** (not the server `_anno_panels.html`)
so cards + timeline react to accept/edit/delete without reloads. The page
serializes the draft items to JS arrays — `draftMarkers`, `draftFields`,
`draftNotes` — each item carrying `item_id`, the editable values, and a
**`status`** of `"accepted"` or `"proposed"`.

- `status` is derived from the review_item `decision`: `accepted → "accepted"`,
  otherwise `"proposed"`. (No schema change; `build_draft_view` already exposes
  `decision`.)
- **Rejected items are excluded** from the draft set (so Delete makes a card
  vanish). `build_draft_view` (or its caller) filters out `decision == "rejected"`.
- `player.js` already takes `draftMarkers` as Alpine data; `draftFields` /
  `draftNotes` are added to the page's `x-data` the same way. A small
  serializer (extend `_build_draft_for_clip` / a view-model helper) maps the
  `build_draft_view` panels to these arrays with `status`.

### One review component (evolve `reviewQueue` → review mixin)

Today `reviewQueue` reads decisions off DOM checkboxes. The redesign replaces
that with a data-driven mixin (kept in `review.js`, composed into the clip
`x-data` alongside `player` + `clipAnnotate`) operating on the draft arrays:

- `totalCount()` / `acceptedCount()` — counts across the three arrays.
- `acceptAll()` — set every item `status = "accepted"` and persist each
  (`decision:accepted`).
- `toggleAccept(item)` — flip proposed↔accepted; persist the new decision.
- `del(item)` — `decision:rejected`, splice from its array, toast "Proposal
  deleted." (Delete = reject.)
- `toggleEdit(item_id)` — open/close the inline editor; for markers, set
  `editingItemId` (drives the purple timeline highlight) and `seek` to the in-point.
- Marker edits (name/category/description + dragged in/out) persist through the
  **existing** `player._persistMarker` (full `edited_value`, `decision:accepted`);
  field/note edits persist via `decision:accepted` + `edited_value`.
- `navClip(±1)` — move within the review queue, **preserving draft scope**
  (navigate to `/clips/<id>?review=1&scope=draft`).
- `applyDraft()` — `POST /api/review/clips/{id}/apply` (JSON path, **no** HX
  header), toast success, stay on the clip. Because the panel is now
  Alpine-data-driven, applied items are reflected by refreshing the **client
  arrays** (see "Draft refresh") — not by swapping the server partial into
  `#draft-aside` (that would clobber the reactive state). The accepted cards
  remain visible as Accepted until the refresh marks them applied / drops them.

All persistence reuses `POST /api/review/items/{id}/decision` and
`POST /api/review/clips/{id}/apply` — no new endpoints for the review actions.

### Draft refresh after apply / annotate

The panel renders from the page's Alpine draft arrays, so flows that previously
re-injected the server `_anno_draft.html` partial must instead refresh those
arrays:

- **After Apply:** re-fetch the draft data and replace the arrays (applied
  items, now `applied_at`-set, drop out of the draft set), then toast. A small
  **JSON draft endpoint** (`GET /api/clips/{id}/draft-data`, returning the same
  `markers`/`fields`/`notes` arrays with `item_id` + `status`) backs this
  in-place refresh without a full reload.
- **After Annotate** (the `clipAnnotate` "Annotate" action that produces a new
  draft): reuse the same JSON refresh to repopulate the arrays. (If the existing
  flow swapped `_anno_draft.html`, it is repointed at the JSON refresh.)

This keeps a single source of truth (the Alpine arrays) and avoids mixing
server-rendered HTML into a reactive subtree.

### Templates

- Rework `pages/_anno_draft.html` (the `#draft-aside` content) into the
  card layout: the draft chip + the Markers/Fields/Notes tabs + an `x-for`
  of `.ri-card`s per array. Each card: `.ri-state` chip (Proposed/Accepted),
  full text, an `.ri-editor` (shown when `editingItemId === item_id`), and
  `.ri-actions` (Accept / ✎ Edit / Delete). An `.edit-hint` explains the
  timeline-drag flow. (CSS `.ri-*` / `.edit-hint` from the design → `app.css`.)
- The **review bar** replaces the current `review-actionbar` markup: Accept all
  · `✓ N/M` · ‹ `Clip i/N` › · Apply (N), shown only in draft scope.
- **Timeline** (`_player_overlay.html` / the transport markup in
  `clip_detail.html`): in draft scope, render draft ranges with
  `:class="{ editing: editingItemId === m.item_id, 'is-accepted': m.status === 'accepted' }"`
  and **hide the published range row** (`x-show="scope === 'published'"`). Add the
  3-color `.draft-range` / `.is-accepted` / `.editing` rules from the design to
  `app.css`.

### Batch → review queue

The Batches **Review →** (currently `/clips/<first>?review=1`) becomes a small
JS action that:

1. `GET /batches/review-queue?job_ids=<csv>` → ordered list of the batch's
   **pending** clip ids (new route in `routes/batches.py`, backed by a repo
   method `pending_clip_ids_for_jobs(job_ids)` that aggregates
   `review_items.applied_at IS NULL` clips across the batch's jobs, ordered).
2. Writes them to `sessionStorage["catdv:reviewQueue"]` (the existing queue key).
3. Navigates to `/clips/<first>?review=1&scope=draft`.

`reviewQueue`/`navClip` then walks that queue exactly as the clips-list "Review
selected" path already does. The clip-detail page reads the queue from
sessionStorage as today (no per-clip server queue needed).

### Apply / decision offline behavior

Decisions and apply route through `get_core_ctx` (DB) + the write queue, so they
work offline: decisions persist immediately; apply enqueues `pending_operations`
that drain when the sync engine reconnects (unchanged from today). Failures
toast; no `location.reload`.

## Reuse map (no duplication)

| Need | Reuse |
|---|---|
| Scope toggle (Published/Draft) | `clip_detail.html` (`scope`, `review_mode`) |
| Draft-marker timeline drag-edit + persist | `player.js` `startMarkerDrag` / `editingItemId` / `nudgeMarker` / `_persistMarker` |
| Per-item accept/reject/edit persistence | `POST /api/review/items/{id}/decision` |
| Apply (enqueue accepted) | `POST /api/review/clips/{id}/apply` (JSON path); applied state refreshed via the new `GET /api/clips/{id}/draft-data` |
| Clip-walk queue | `reviewQueue` (`sessionStorage["catdv:reviewQueue"]`, `?review=1`) |
| Draft item shapes (item_id, decision) | `build_draft_view` |
| Batch pending clips | `ReviewItemsRepo.list_pending_clips(job_id=…)` pattern → `pending_clip_ids_for_jobs` |
| Subtree re-init after apply swap | `window.htmxAlpine.reinit` |
| Toasts | `Alpine.store('toast')` |

## Error handling

- **Decision/apply failure** → toast (reuse the existing `review.js` error
  toasts); the card keeps its prior state.
- **Empty draft** (review mode, nothing pending) → existing empty-state +
  auto-advance behavior in `clip_detail.html` is preserved.
- **Review-queue fetch fails** (offline catalog) → Review → still opens the
  first clip in review mode; the walk falls back to a single-clip queue and
  toasts that the rest of the set couldn't load.
- No `alert()` / silent `.catch` / `location.reload`.

## Testing

Backend (pytest):

- `build_draft_view` (or the serializer) excludes `decision == "rejected"`
  items and exposes `decision` so the page can map `status`.
- `pending_clip_ids_for_jobs` returns the ordered un-applied clip ids across a
  batch's jobs (and the studio-exclusion / dedupe behavior matches
  `list_pending_clips`).
- `GET /batches/review-queue?job_ids=…` returns that ordered list (200), and
  503/empty handling.
- `GET /api/clips/{id}/draft-data` returns the `markers`/`fields`/`notes`
  arrays with `item_id` + `status`, excluding `rejected` items.
- The existing decision/apply route + `run_job`/review tests stay green.

Frontend / integration:

- Card render through the shared env (template smoke test).
- Manual acceptance flows below (accept/edit/delete/accept-all/apply,
  3-color timeline, clip-walk).

## Manual acceptance flows

1. **Open review from a batch.** On `/batches`, click a batch's **Review →**.
   *Expected:* lands on the first pending clip in **Draft** scope; the review
   bar shows `Clip 1 / N`; proposals render as readable cards (full name +
   description), each chipped **Proposed**.

2. **Accept / Accept all.** Click **Accept** on a card → its chip flips to
   **Accepted** and its timeline bar turns **yellow**; the count reads `✓ 1/M`.
   Click **✓ Accept all** → every card Accepted, all draft bars yellow, `✓ M/M`.

3. **Edit a marker on the timeline.** Click **✎ Edit** on a marker card → the
   editor opens, the card + its timeline bar highlight **purple**, and dragging
   the bar's edges updates the in/out readout. Reload → the edit persists
   (it was saved as `edited_value`, accepted).

4. **Delete (reject).** Click **Delete** on a proposal → the card disappears,
   the count drops, a "Proposal deleted" toast shows. It does not come back on
   reload and is excluded from Apply (but the row still exists in the DB).

5. **Apply + stay.** With some Accepted, click **Apply (N)** → a success toast;
   the draft panel re-renders in place (no full reload); the clip stays open.

6. **Clip-by-clip walk.** Click **›** → the next clip in the set opens straight
   in **Draft** scope (`Clip 2 / N`); **‹** goes back. Prev is disabled on the
   first clip, Next on the last.

7. **Published unchanged.** Toggle to **Published** → the existing
   Markers/Fields/Notes/History panels render as before; the draft cards and
   3-color timeline are hidden.

8. **Single-clip review still works.** Open a clip directly with a draft and
   switch to Draft (no batch queue) → cards + accept/edit/delete/apply all work;
   the ‹ › arrows reflect a single-item queue.
