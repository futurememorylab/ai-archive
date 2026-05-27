# Draft Review & Accept — consolidated review queue + bulk apply

**Date:** 2026-05-27
**Status:** Approved (design)
**Depends on:** existing `review_items` table, `/api/review/*` endpoints,
`WriteQueue` / `pending_operations` / `SyncEngine` apply path.

## Problem

Gemini annotation jobs already produce drafts: each job writes one
`Annotation` plus one `review_items` row per proposed marker / field /
note, every row starting at `decision = 'pending'`. The clip detail page
(`/clips/{id}`) renders these on its **Draft** tab — but **read-only**.
There is no UI anywhere that calls the decision or apply endpoints, so a
human literally cannot accept a draft. The backend accept/reject/apply
machinery exists and is unused.

Two gaps follow:

1. **No way to accept.** The draft is visible but inert.
2. **No consolidated view.** Nothing lists which clips have unaccepted
   drafts, so a reviewer can't see or work through a backlog. Reviewing
   one clip then returning to the clips list to find the next one wastes
   most of the reviewer's time.

This spec adds the review UI on top of the existing storage/apply
machinery, for both video and image clips.

## Background — the machinery that already exists

This is **not** a storage redesign. The accept→upstream pipeline is
built and working; we are wiring a UI to it.

- `review_items(decision, edited_value, applied_at)` — per-clip,
  per-item human-review queue. `kind ∈ {marker, field, note}`.
- `POST /api/review/items/{id}/decision` — set accept/reject/pending +
  optional `edited_value`. **Local-only**; never touches CatDV.
- `POST /api/review/clips/{id}/apply` — groups *accepted* items into
  ChangeOps and enqueues `pending_operations` rows; `SyncEngine` drains
  them to CatDV (immediately if online, queued if offline). Markers
  batch into one `AddMarkers`, fields into `SetField`, notes into
  append/replace.
- `WriteQueue.enqueue_apply` already skips items with
  `applied_at IS NOT NULL`, so double-applies are idempotent.

Every `review_item` traces to a **batch** via
`annotation.job_id` (a `Job`: prompt version, created_at, notes). That
is what "the last batch" means.

## Goals

- A consolidated **`/review` page** listing every clip with un-applied
  drafts, modeled on the existing Cache page (metric strip → filters →
  bulk bar → shared table). Reachable from a new rail icon with a
  pending-count badge.
- A **human-in-the-loop (HITL)** path: open a clip into a review
  *queue*, accept/edit/reject items on the existing Draft panel, and
  advance clip-to-clip with **Apply & next** — never bouncing back to a
  list. Auto-apply on advance.
- A **yolo (bulk) path**: select clips on `/review`, optionally filter
  by item **kind**, and apply all matching drafts in one action without
  visiting each clip.
- **Images** work identically to video everywhere (drafts, list, queue,
  bulk apply), minus time-based markers.
- Stay **offline-safe** and **simple** — reuse the Cache page's
  selection model and the clip page's player/panels; add no second
  renderer.

## Non-goals

- No storage/schema redesign of `review_items` or the apply pipeline.
- No per-item bulk grid (ticking individual items across many clips).
  Bulk granularity stops at **kind** (Markers / Fields / Notes).
- No new player, timeline, or panels renderer. The HITL path reuses
  `clip_detail.html`, `_anno_draft.html`, `_anno_panels.html`.
- No change to how jobs run or how drafts are generated.
- No general "batch actions on videos" framework yet — but the
  selection model + `apply-batch` endpoint are shaped to grow into one.
- No marker support for images (stills have no timeline).

## Design

### Decisions captured during brainstorming

- **HITL = the clip page in review mode**, not a new screen. Avoids a
  duplicate player/panels renderer (CLAUDE.md forbids parallel-evolving
  a second renderer).
- **Apply timing: auto-apply on "next".** Clicking *Apply & next* pushes
  the clip's accepted items upstream, then advances. Rejected items are
  dropped. Nothing is left staged.
- **Item default: pre-accepted, opt-out.** Every draft item starts
  accepted; the reviewer only unticks/edits the wrong ones. "Review by
  exception" — fewest clicks when Gemini is mostly right.
- **Yolo = select clips + apply.** Bulk multi-select on `/review`, with
  a kind filter, applies all matching un-applied items on the chosen
  clips. No per-clip visit.
- **Batch = a Job.** Filtering `/review` by batch (incl. "last batch")
  is filtering by `annotation.job_id`.

### Backend additions

All additive; no existing endpoint changes behavior.

1. **Cross-clip pending query** — new `ReviewItemsRepo` method, e.g.
   `list_pending_clips(conn, *, job_id=None, media_kind=None, limit, offset)`,
   returning one row per clip with un-applied items:
   `{catdv_clip_id, catdv_clip_name, media_kind, marker_count,
   field_count, note_count, prompt_version_id, prompt_name, version_num,
   job_id, created_at}`. "Un-applied" = `applied_at IS NULL` (see
   "Counts semantics" below). Backed by a `GROUP BY catdv_clip_id` over
   `review_items` joined to `annotations` (for `job_id` / kind) and the
   clip cache (for name / `media_kind` / thumbnail).

2. **`GET /api/review/pending`** — paginated list for the table region
   (HTMX-swappable), plus summary counts for the metric strip. Honors
   `job_id` and `media_kind` filters.

3. **`POST /api/review/apply-batch`** —
   `{clip_ids: [int], kinds: ["marker","field","note"]}`. For each clip:
   set every un-applied `review_item` whose `kind ∈ kinds` to `accepted`,
   then call the **existing** `WriteQueue.enqueue_apply` for that clip
   (reusing the same annotation/target_map/etag/fps resolution as
   `routes/review.py::apply_clip`). Returns `{clips: N, queued: M}`.
   `kinds` defaults to all three when omitted.

4. **Pending badge count** — a lightweight count (distinct clips with
   un-applied items) exposed for the rail badge. Reuse the existing
   topbar/pills context plumbing rather than a bespoke endpoint where
   possible; a `GET /api/review/pending/count` is acceptable if that
   plumbing doesn't already render on every page.

**Counts semantics.** A clip is "awaiting review" if it has ≥1 item with
`applied_at IS NULL` (regardless of current decision — a pending or
even locally-accepted-but-not-yet-applied item still needs action).
Per-kind counts on a row count those same un-applied items.

### UI surface A — `/review` page (mirror of the Cache page)

Reuses the Cache page skeleton and partials: `_video_list.html` scaffold
with `_review_head_cells.html` / `_review_row_cells.html`, the shared
`_pager.html`, and an Alpine selection model `reviewSel()` cloned from
`cacheSel()` (checkbox `.row-check`, `#row-select-all`, bulk bar shown
when `count > 0`, HTMX `afterSwap` recount).

- **Metric strip:** Clips awaiting review · Markers pending · Fields
  pending · Notes pending · Last batch (job label + age).
- **Filters (collapsible `<details>`, GET params):**
  - **Batch** — All / Last batch / specific job (dropdown from
    `JobsRepo.list_jobs`). Maps to `job_id`.
  - **Media type** — All / Video / Image. Maps to `media_kind`.
- **Table row:** `row-check` checkbox · thumbnail (shared thumb) · name
  (links to the clip's review queue) · type · counts
  ("4 markers · 2 fields · 1 note") · prompt v# · batch/age.
- **Bulk action bar** (appears on selection):
  - **Kind toggles:** ☑ Markers ☑ Fields ☑ Notes (all on by default) —
    the item-level filter for bulk apply.
  - **Buttons:** `Review selected →` (opens HITL queue scoped to the
    selected clip ids), `Apply drafts (selected)` (calls
    `apply-batch` with selected `clip_ids` + ticked `kinds`, behind a
    confirm), `Clear`.
- Empty state when nothing is pending ("No drafts awaiting review").

### UI surface B — HITL review queue (reuse `/clips/{id}`)

- **Entering the queue:** "Review selected →" (or clicking a row name)
  stores the ordered clip-id list in `sessionStorage`
  (e.g. `catdv:reviewQueue`) and navigates to the first clip with
  `?review=1`. With no explicit selection, the queue is the full
  current-filtered pending list.
- **Review mode rendering:** when `?review=1`, the clip page opens on
  the **Draft** scope and renders the Draft panel with per-item
  controls. Achieved by passing a `review_mode` flag into
  `_anno_draft.html` / `_anno_panels.html`; published-mode rendering is
  unchanged.
- **Per-item controls** (added to `_anno_panels.html`, gated by
  `review_mode`):
  - Each marker / field / note row gets an **accept toggle, pre-checked**.
  - Inline **edit** (writes `edited_value` via the existing
    `decision` endpoint with `decision='accepted'`).
  - **Reject** = untick (sets `decision='rejected'` locally).
  - Decisions persist to the local DB as the reviewer toggles (calls
    `POST /api/review/items/{id}/decision`); nothing goes upstream yet.
- **Review action bar** on the clip page (review mode only):
  - Progress "3 / 12".
  - **`Apply & next →`** — `POST /api/review/clips/{id}/apply` (pushes
    accepted items), then advance to the next clip id in the queue with
    `?review=1`. At the end of the queue, return to `/review`.
  - **`Skip`** — advance without applying (clip stays pending).
  - **`prev`** — step back in the queue.
- **Images:** the Draft panel shows Fields / Notes only; the Markers tab
  is empty/omitted when the clip has no duration (the page already
  guards the transport/timeline on `clip.duration_secs`, and
  `target_map.expand` already drops time-based markers without a
  duration). No image-specific branch in the review logic.

### Rail entry point

Add a **Review** icon to `_rail.html` (new `icons/_review.svg`) linking
to `/review`, with a count badge = clips awaiting review. Badge hidden
at zero. `rail_active = "review"` on the `/review` page.

### Data flow summary

```
job runs ─▶ annotations + review_items (decision=pending, applied_at=NULL)
                         │
        ┌────────────────┴───────────────────┐
   HITL path                              yolo path
   /clips/{id}?review=1                   /review (select clips + kinds)
   toggle/edit items                      POST /api/review/apply-batch
   POST decision (local)                  (accept matching + enqueue)
   Apply & next ▶ POST clips/{id}/apply        │
        └────────────────┬───────────────────┘
                  WriteQueue.enqueue_apply
                  ▶ pending_operations ▶ SyncEngine ▶ CatDV
                  (online: now · offline: queued)
```

### Error / edge handling

- **Apply with nothing accepted** (all unticked or Skip): no-op apply
  returns `{queued:0}`; clip stays in the pending list.
- **Offline:** `apply` / `apply-batch` enqueue and return success; the
  list and badge already reflect local state (the query is a DB lookup,
  no network), so `/review` is fully usable offline. Items leave the
  pending list once `applied_at` is set, independent of drain timing.
- **Idempotency:** re-applying a clip is safe — `enqueue_apply` skips
  `applied_at IS NOT NULL` items.
- **Stale queue:** if a clip in the sessionStorage queue is already
  applied (e.g. yolo'd in another tab), review mode shows the empty
  draft state; Skip/next still advances.
- **Concurrent edits to the same clip:** existing `expected_etag` on the
  enqueued op guards against applying onto a changed clip; surfaced
  through the existing sync/pending-ops error path (unchanged here).

## Files (anticipated)

- `backend/app/repositories/review_items.py` — `list_pending_clips`,
  pending-count query.
- `backend/app/routes/review.py` — `GET /pending`, `POST /apply-batch`,
  pending-count.
- `backend/app/routes/pages/` — `/review` page handler + queue-entry
  wiring on the clip page (`?review=1`).
- `backend/app/templates/pages/review.html` (new),
  `_review_head_cells.html`, `_review_row_cells.html` (new).
- `backend/app/templates/pages/_anno_panels.html`,
  `_anno_draft.html` — `review_mode` controls.
- `backend/app/templates/pages/clip_detail.html` — review action bar.
- `backend/app/templates/pages/_rail.html` + `icons/_review.svg`.
- `backend/app/static/` — `reviewSel()` + queue navigation JS.
- DB: no schema change anticipated (reuses `review_items`); confirm a
  `decided_at` / `applied_at` column already exists (it does).

## Manual acceptance flows

Run against a live app with at least one completed (non-studio)
annotation job over a mix of video and image clips, producing pending
drafts.

1. **See the backlog.** From any page, the rail shows a **Review** icon
   with a count badge equal to the number of clips with un-applied
   drafts. Click it → `/review` lists those clips with thumbnails,
   types, and per-kind counts; the metric strip totals match the row
   counts.

2. **Filter by batch ("yolo the last batch").** On `/review`, open
   Filters → set Batch to "Last batch" → Apply. The list narrows to
   clips from the most recent job. Set Media type to Image → only image
   clips remain.

3. **Yolo bulk apply, kind-filtered.** Select all listed clips. In the
   bulk bar, untick **Fields** and **Notes** (leave Markers). Click
   **Apply drafts (selected)** → confirm. Result: each selected clip's
   marker drafts are applied to CatDV; fields/notes remain pending. Open
   one applied clip → its Published markers now include the accepted
   ones; the clip's field/note drafts are still on the Draft tab. The
   `/review` badge/count drops accordingly.

4. **HITL review of one clip.** Back on `/review`, click a video clip's
   name → lands on `/clips/{id}?review=1`, Draft scope, with every item
   pre-checked. Untick one bad marker; edit one field's value; leave the
   rest. The timeline still shows draft ranges and the player still
   plays (regression guard on the existing player).

5. **Apply & next advances the queue.** Click **Apply & next →**. The
   accepted+edited items are pushed (open the clip in another
   tab/Published to confirm), and the page advances to the next pending
   clip in the queue with progress "2 / N". Reaching the end returns to
   `/review`. The unticked marker did **not** get applied.

6. **Skip leaves a clip pending.** On some clip, click **Skip** → it
   advances without applying; that clip remains in `/review`.

7. **Image clip in HITL.** Open an image clip's review → the Draft panel
   shows Fields / Notes only, no marker timeline, no broken player.
   Accept a field, Apply & next works the same as video.

8. **Offline safety.** Disconnect CatDV (or simulate offline). `/review`
   still loads with correct counts. Yolo-apply a clip → returns success;
   the clip leaves the pending list. On reconnect, the SyncEngine drains
   and the markers appear in CatDV. (Regression guard: the page never
   errors out when CatDV is unreachable.)

9. **Idempotency.** Re-open an already-applied clip with `?review=1` and
   click Apply & next again → no duplicate markers appear upstream.
