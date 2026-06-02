# New-batch picker — in-page two-pane clip selection for the Batches hub

**Date:** 2026-06-02
**Status:** Approved (design)

Builds on the Batches hub (`docs/specs/2026-06-02-batches-hub-design.md`,
branch `feat/batches-hub`). Origin: a second Claude Design handoff
(`batches.html`) plus direct user feedback. This is **Spec A** of two; the
clip-by-clip **review walk** (`clip.html`) is a separate follow-up spec.

## Problem

The v1 Batches hub shipped "+ New batch" as a **redirect to the clips
list** (`href="/"`), where the user selects clips with the existing
checkbox/bulk-annotate flow. Two problems with that, both reported:

1. **It teleports you away** from the Batches hub — a jarring context
   switch for what should be a focused "pick clips → start a run" action.
2. **Selection does not survive paging.** The clips list paginates
   server-side and selection lives in DOM checkboxes (`row_select.js`); a
   page navigation re-renders the table and the checkboxes reset. Picking
   two clips that fall on different pages is impossible — "select one, go
   to the next page, the first is forgotten."

The new design replaces the redirect with an **in-page two-pane picker
modal**: a paginated clip list on the left and a **persistent "Selected"
basket** on the right, backed by a client-side selection map that survives
paging and filtering, so "what have I selected?" is always answered.

## Goals

- "+ New batch" opens a **modal in place** (no navigation away from
  `/batches`).
- A **two-pane** modal: paginated clip list (left) + persistent
  **"Selected (N)" basket** (right) with per-clip remove (✕) and Clear.
- **Selection persists across pages, filters, and the search box** — held
  in a client-side map keyed by clip id, not in the rendered checkboxes.
- The clip list is **server-paginated** (reuses the clips-list query +
  `clip_summary` + `_video_list.html`), so the whole CatDV catalog is
  reachable, not a bounded client snapshot.
- Reuse of the existing **search + cache + anno** filters; a **"Selected
  only"** view that shows just the current picks.
- Per-kind **production-prompt** assignment (one prompt per media kind
  present in the selection), then **Start batch** creates the run by
  reusing the existing per-kind `POST /api/jobs` + `run_group` machinery.
- Starting a batch drops it into the hub's table and it live-updates via
  the existing jobs SSE refresh.

## Non-goals

- The clip-by-clip **review walk** / `clip.html` redesign and the batch
  **Review →** "open first clip" behavior — separate follow-up spec.
- Changing the clips-list **"Annotate selected"** flow; it stays as a
  second, independent entry point to batch creation.
- Server-side **kind** and **decade** filters in the picker. The CatDV
  list query has no kind/decade predicate (only `cache` / `anno`); search +
  cache + anno + the basket cover selection, and `kind` still drives the
  per-kind prompt assignment. Deferred (possible later clips-list
  enhancement).
- Per-clip prompt overrides, multi-prompt-per-kind (one prompt per kind).
- Offline batch creation — the picker needs CatDV for the catalog; it
  surfaces a clear 503, it does not silently no-op.
- Persisting the in-progress selection across a page reload (the modal's
  selection is session-local to the open modal, matching the prototype).

## Design

### Entry point

The header button changes from a link to an action:
`<button class="btn primary" @click="openPicker()">+ New batch</button>`.
The page's existing Alpine controller (`batchesPage()`, from v1) gains the
picker state + methods. The clips-list seed path (v1 honored
`sessionStorage["catdv:batchQueue"]` / `?new=1`) is preserved: if a seed is
present on load, the picker opens pre-populated.

### Modal layout (two-pane)

A `.modal` hosted in the page (same pattern as v1's modal), card class
`nb-card` (≈960px). `.nb-body` is a flex row:

- **`.nb-main` (left, flex:1):**
  - `.nb-filters`: a search input (`q`), the **cache** `<select>`
    (any/none/local/ai), the **anno** `<select>`
    (any/none/for_review/applied), and a **"Selected only"** checkbox.
  - `#batch-picker-list`: the fetch target for server-rendered picker rows.
  - `.nb-pager`: ‹ Prev · "1–N of M" · Next › + an "N match" count.
- **`.nb-side` (right, 304px):**
  - `.nb-side-h`: "Selected" + live count + **Clear** (when >0).
  - `.nb-basket`: one `.nb-bchip` per selected clip (thumb + name + kind +
    ✕), or an empty-state hint. Rendered by Alpine `x-for` over the
    selection map values.
  - `.nb-prompts` (shown when ≥1 kind selected): one `.nb-prow` per media
    kind present in the selection — a `tag` (kind) + a `<select>` of
    compatible production prompts ("— skip this kind —" default).
- **`.modal-actions` footer:** Cancel · primary
  "▶ Start batch — N clips" (disabled until ≥1 kind has a prompt assigned).

Styles come from the design's `.nb-*` rules (added to `app.css`, tokens
only — no raw hex). `[x-cloak]`, `.btn`, `.pill`, `.tag`, `.thumb`,
`.search` already exist.

### Server — picker rows

New `GET /batches/picker` in `routes/batches.py` (depends on
`get_live_ctx` → typed 503 offline, since it lists the CatDV catalog).
Params: `q`, `cache`, `anno`, `offset`, `limit` (default 12). It runs the
**same clip-page query as the clips list** and renders a picker rows
partial.

To avoid duplicating the clips-list orchestration, extract a shared helper
used by both `routes/pages/clips.py::clips_list` and the picker:

```
query_clip_page(ctx, *, q, offset, limit, cache, anno) -> (rows, total)
```

where `rows` is a list of `clip_summary(clip, cache_status=…)` dicts and
`total` is the page total. It encapsulates: `normalize_cache/anno` →
`resolve` filters (when active) or plain `archive.list_clips` →
bulk `cache_inspector.status_for_clips` → `clip_summary` per clip. The
clips-list route keeps layering its extras (draft labels, batch status,
pager view-model) on top of this base; the picker uses the base rows as-is.

The picker partial reuses `_video_list.html` with picker-specific cells:

- `pages/_batch_picker_head.html` — trailing `<th>`s: Year · Type.
- `pages/_batch_picker_cells.html` — trailing `<td>`s: `.col-year` (mono)
  and `.col-type` (mono) = `row.kind`. **No `row_href`** (rows are for
  selection, not navigation), `colspan=5`, `empty_msg="No clips match."`.

A `GET /batches/picker` returns `_video_list.html` rendered with these +
a small pager context (`offset`, `limit`, `total`, `q`, `cache`, `anno`).
The pager metadata is returned alongside so the client can update "1–N of
M" and disable Prev/Next (either as data attributes on the list wrapper or
a tiny JSON header — implementation detail for the plan).

### Selection model (client)

The picker holds selection in an Alpine map `sel` (`{ [id]: {id, name,
kind, thumb} }`), **not** in the checkboxes:

- **Capture on tick.** A delegated `change` listener (scoped to the open
  modal) on `.row-check` reads the clip id from the checkbox `value`
  (`"catdv/<id>"`) and the **name / thumb / kind from the row DOM** (`tr
  .name`, `tr img.thumb`’s `src`, `tr .col-type`) — the same DOM-reading
  approach `bulkAnnotate.js` uses for `.col-type`. Ticked → store the
  object in `sel`; unticked → delete. `#row-select-all` toggles every row
  on the current page.
- **Basket renders from `sel`** (`Object.values`), independent of which
  page/filter is showing — so off-page picks stay visible. ✕ removes one;
  Clear empties `sel`.
- **Re-apply on (re)render.** After each picker fetch (page change,
  filter, search), inject rows into `#batch-picker-list`, call
  `window.htmxAlpine.reinit(list)`, then set each visible `.row-check`’s
  `checked` from `sel` and reconcile `#row-select-all`.
- **"Selected only"** swaps the left list for the **same `.nb-bchip` chip
  list** rendered from `sel` (client-side, no server fetch; selection is
  bounded), so there is **no second row renderer** — it reuses the basket
  chip. Toggling it off resumes the server list.

### Start batch

Reuses the bulk-annotate logic (`bulkAnnotate.js`) verbatim in shape:

1. Group `sel` clips by `kind`.
2. For each kind with an assigned `prompt_version_id`, `POST /api/jobs`
   with `{ prompt_version_id, clip_ids, auto_start: true, run_group }` —
   one shared `run_group` (a `crypto.randomUUID()`) across the per-kind
   jobs, so the hub groups them into one batch row.
3. On success: close the modal, toast (`Alpine.store('toast')`), and
   refresh the hub table (dispatch the existing `jobs-changed` event /
   trigger the v1 `refresh()`), so the new batch appears immediately.
4. On failure (any job not started, e.g. services offline): keep the modal
   open and toast the error (reuse the bulk-annotate failure message
   shape).

Production prompts are loaded once when the picker opens, via
`GET /api/prompts?archived=0` (keep `current_production_version_id != null`,
filter per kind by `media_kind == kind or "any"`) — identical to
`bulkAnnotate.js::_loadPrompts`.

### Removed v1 behavior

The v1 "+ New batch" `href="/"` redirect and any reliance on the clips-list
DOM-checkbox selection for batch creation are removed in favor of the
modal. The clips-list page itself is unchanged.

## Reuse map (no duplication)

| Need | Reuse |
|---|---|
| Clip-page query (filters, listing, cache status) | extracted `query_clip_page` shared with `clips_list` |
| Row scaffold (checkbox, cache badge, thumb+name) | `_video_list.html` + `clip_summary` |
| Per-row kind for selection capture | `.col-type` cell (as `bulkAnnotate.js` does) |
| Production-prompt load + per-kind filter | `GET /api/prompts?archived=0` (`bulkAnnotate.js` logic) |
| Job creation per kind + run_group | `POST /api/jobs` (unchanged), `JobsRepo.create_job`, `annotator.run_job` |
| Live table refresh after start | existing jobs SSE + `jobs-changed` (v1 `batchesPage`) |
| Modal host, fetch-subtree reinit, toasts, tokens | `#modal-root`/page modal, `window.htmxAlpine.reinit`, `Alpine.store('toast')`, `:root` tokens, `.nb-*` from design |

## Error handling

- **Catalog offline / CatDV down:** `GET /batches/picker` → typed 503; the
  modal shows an inline message + toast ("Catalog unavailable — connect to
  load clips"). The hub table behind it stays fully rendered (read path is
  offline-safe).
- **Start with services offline:** mirrors `bulkAnnotate.js` — `POST
  /api/jobs` returns `started:false`; the modal stays open and toasts which
  kinds failed.
- **No production prompts for a kind:** that kind’s `<select>` shows only
  "— skip this kind —"; Start counts only runnable (assigned) kinds, and
  its label reflects the runnable count.
- **Empty selection:** Start is disabled and reads "Select clips".
- **No frontend `alert()` / silent `.catch` / `location.reload`** — toasts
  + partial swaps only.

## Testing

Backend (pytest):

- `query_clip_page` returns `(rows, total)` with `clip_summary`-shaped rows
  and correct totals; the existing clips-list route still renders
  identically after the extraction (existing `test_routes_pages.py` /
  `test_clips_page_perf.py` stay green — the N+1 pin must still hold).
- `GET /batches/picker`: 200 renders picker rows (checkbox `value`,
  Year/Type cells) for a fake archive page; honors `q`/`offset`/`limit`;
  returns the pager total; **503 when offline** (no live ctx).
- Reuse existing `/api/jobs` creation tests for Start (unchanged endpoint).

Frontend / integration:

- Selection persistence, basket, "Selected only", and Start are JS —
  covered by the manual acceptance flows below.

## Manual acceptance flows

1. **No teleport.** On `/batches`, click **+ New batch**. *Expected:* a
   two-pane modal opens in place; the URL does not change; the hub table is
   still behind it.

2. **Cross-page selection persists.** In the picker, tick a clip on page 1,
   click **Next ›**, tick a clip on page 2. *Expected:* the right-hand
   **Selected (2)** basket lists both clips (thumb + name + kind) the whole
   time; the count reads 2; paging back shows page 1's clip still ticked.

3. **Search/filter keeps selection.** With 2 clips selected, type a search
   term and change the cache filter. *Expected:* the left list refetches and
   filters; the basket still shows both picks; clearing the search keeps
   them.

4. **Remove + Clear.** Click a basket chip's ✕ — that clip leaves the
   basket and, if visible, its row unticks. Click **Clear** — basket empties
   and visible rows untick.

5. **Selected only.** Tick "Selected only". *Expected:* the left list shows
   exactly the selected clips (as chips); untick a clip there and it leaves
   the basket; toggling off resumes the full paginated list.

6. **Per-kind prompts + Start.** Select clips of one or more kinds; assign a
   production prompt per kind in the basket pane. *Expected:* the Start
   button reads "▶ Start batch — N clips" (runnable count); clicking it
   closes the modal, toasts success, and a new **Running** batch appears at
   the top of the hub table and advances live.

7. **Offline catalog.** With CatDV offline, open the picker. *Expected:* an
   inline "catalog unavailable" message + toast; the hub table behind it
   still renders. Starting is not possible until reconnected.

8. **Clips-list flow unaffected.** The clips-list **Actions → Annotate
   selected** path still creates a batch exactly as before, and it appears
   in the hub.
