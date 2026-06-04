# Studio archive picker — reuse the batch picker list

**Date:** 2026-06-04 (v2 — same day: scope extended to the full picker UX via a shared component)
**Status:** Approved

## Problem

There are two clip-picker lists in the app:

- The **New-batch picker** (`batches.html` + `GET /batches/picker`)
  renders rich rows through the shared `_video_list.html` scaffold:
  select-all + per-row checkboxes, cache badge, thumbnail + name,
  year / duration / type cells, server pagination, text search,
  cache/anno filters, a "Selected only" toggle, and a selected-clips
  basket sidebar.
- The **Studio archive picker** (`_studio_archive_picker.html` +
  `GET /studio/_archive_picker`) originally rendered bare `picker-row`
  labels: checkbox, name, `id:42`. No thumbnails, no cache badges, no
  pagination (hard 50 cap), no metadata.

The studio one was a significantly worse parallel renderer of the same
concept. Per the project's reuse discipline (CLAUDE.md "Frontend:
explore before implementing"), the duplicate must go.

## Goals

- One renderer and one route for "a pageable, searchable, selectable
  list of catalog clips": the existing `/batches/picker` endpoint and
  the `_video_list.html`-based `_batch_picker.html` partial.
- **(v2)** One picker *component*, not just one row renderer: the
  filters row, results list, pager, and selected-clips basket are a
  shared Jinja-partial + shared-JS unit used by BOTH the New-batch
  modal and the studio archive-picker modal. The studio modal gets the
  full picker UX (filters, "Selected only", basket); only the footer
  action differs (Add to folder vs Start batch).
- The bare `picker-row` markup, its now-dead `.picker-row` CSS rules
  in `app.css`, and the studio-side list-rendering route branch are
  **deleted** (done in v1).

## Non-goals

- No changes to the batch picker **UX** — its internals are rewired
  onto the shared core, but every visible behavior stays identical.
- No changes to `/batches/picker` beyond the (already-landed)
  docstring note.
- No new server routes; the studio shell route stays as landed in v1.

## Locked decisions

- **v1 — Approach A** (of three considered): the studio modal shell
  stays a studio partial; its results region fetches `/batches/picker`
  directly. Rejected then: (B) studio route renders the shared partial
  itself — duplicates ~20 lines of route plumbing; (C) full shared
  component extraction — judged a refactor, not a tweak, *while the
  studio modal only needed list + pager + search*.
- **v2 — Approach C after all, by user request.** The user wants the
  filters and the basket in the studio modal too. Copying them would
  mean ~150 lines of duplicated JS + ~40 lines of duplicated markup —
  recreating the original problem one level up. So the picker is
  extracted into a shared component and `batchesPage` is rewired onto
  it. The v1 objection (don't rewrite a working picker for a small
  delta) no longer holds when the delta is the whole picker.
- Offline behaviour changes deliberately (v1): `/batches/picker` needs
  live services (typed 503), so the studio modal shows a clear
  "catalog unavailable" message + error toast instead of a silently
  empty list.
- The batch picker's document-level checkbox `change` listener is
  replaced by a container-level `@change` on the shared list region —
  same behavior, properly scoped, and shareable.

## Design (v2)

### Shared markup — two new partials, extracted from `batches.html`

- `pages/_clip_picker_main.html` — the `nb-main` column: filters row
  (search input, cache select, anno select, "Selected only" toggle),
  the `.nb-list` results region (with `@change="onCheckChange($event)"`),
  and the `nb-pager` row. Bindings reference the shared core's state
  names (below).
- `pages/_clip_picker_basket.html` — the basket: "Selected" header with
  count + Clear, the `nb-basket` chip list (`thumb`/name/kind/remove),
  and the empty-state hint.

`batches.html` composes: `nb-body` → include main, then `nb-side` →
include basket + its batch-only `nb-prompts` block. The studio modal
composes: `nb-body` → include main, then `nb-side` → include basket.
Each page keeps its own modal chrome and footer.

The `id="nb-table"` crutch is dropped; all JS resolves the list region
via `$root.querySelector('.nb-list')`.

### Shared JS — `static/clipPicker.js`

`window.clipPickerCore()` returns the picker state + methods, spread
into each page's Alpine component:

- State: `q`, `cacheF`, `annoF`, `selOnly`, `sel` (id → `{id, name,
  kind, thumb}` captured from the row DOM), `offset`, `perPage: 15`,
  `total`.
- Methods: `fetchPage()` (selOnly short-circuit → `_renderSelected`;
  otherwise fetch `/batches/picker` with q/cache/anno/offset/limit,
  inject, `window.htmxAlpine.reinit`, read `#nb-list-meta`, re-apply
  checks; non-OK → escaped `nb-empty` detail + error toast),
  `resetAndFetch()`, `goPage(d)`, `pagerLabel()`, `onCheckChange(e)`,
  `_syncFromCheckbox(cb)`, `_applyChecked(root)`, `_renderSelected(root)`,
  `selCount()`, `selectedClips()`, `selectedKinds()`, `removeSel(id)`,
  `clearSel()`.

`batchesPage()` = `{ ...window.clipPickerCore(), …batch-specific }`
(table refresh/SSE, openPicker, prompt-per-kind, startBatch, seeding).
Its old copies of the core methods and the document-level change
listener are deleted.

`archivePicker(folderId)` = `{ ...window.clipPickerCore(), folderId,
init() { this.fetchPage(); }, addSelected(), close() }`. Selection ids
come from `selectedClips().map(c => c.id)`; the footer counter is
`selCount()`. The v1 `picked` Set is gone.

`clipPicker.js` is added to `layout.html`'s deferred scripts (before
`studio.js`, after `htmxAlpine.js`).

### Routes

Unchanged from v1: the studio shell route renders only the modal
shell; `/batches/picker` serves both modals.

## Testing strategy

- **Refactor under green suite (batches side):** the batch picker's
  behavior is pinned by the existing batches integration tests + perf
  test; the rewire must keep them green with zero markup-visible
  changes.
- **Single-definition guard (new):** a source-scan test asserting the
  core method definitions (`fetchPage(`, `_applyChecked(`,
  `_renderSelected(`, `_syncFromCheckbox(`) each appear in exactly one
  non-vendor file under `static/` + `templates/` — `clipPicker.js`.
  This is the "no second picker" tripwire.
- **Updated v1 tests:** the studio shell route test asserts the shared
  partials' chrome (`nb-filters`, `nb-list`, `nb-pager`, `nb-basket`)
  and still asserts `picker-row` / `hx-get` absent; the studio JS
  source-scan asserts `archivePicker` spreads `clipPickerCore` (the
  endpoint/lifecycle/meta asserts move to a `clipPicker.js` scan).
- **`picker-row` guard** (v1) stays.

## Manual acceptance flows

1. **Add clips via the rich picker.** Setup: app running with live
   CatDV, a studio folder exists. Open `/studio`, expand a folder,
   click "+ Add from archive". Expect: modal opens with the full picker
   UI — search, cache + annotation filter dropdowns, "Selected only"
   toggle, rich rows (thumbnail, cache badge, name, year, duration,
   type), select-all, pager, and a "Selected" sidebar. Tick two clips —
   they appear as chips in the sidebar. Click "Add". Expect: modal
   closes, success toast, the two clips appear in the folder.
2. **Search, filters + pagination.** In the same modal: type a query —
   results re-fetch after a pause; pick "Local" in the cache filter —
   results narrow; reset to "Any cache"; page with Next/Prev — the
   range label updates, and a clip ticked on page 1 stays ticked when
   you return to page 1.
3. **Selection across pages + basket.** Tick one clip on page 1 and one
   on page 2; sidebar shows both chips and "Selected 2"; remove one via
   its chip ✕ — its row checkbox unticks; toggle "Selected only" — the
   list shows just the remaining selection; untoggle; click "Add" —
   the remaining clip lands in the folder.
4. **Offline behaviour.** With CatDV offline (no LiveCtx), open the
   picker. Expect: modal still opens; results region shows a clear
   "catalog unavailable"-style message (not a silent empty list); an
   error toast appears; Cancel still closes the modal.
5. **Batch picker regression check.** Open `/batches`, click
   "+ New batch". Expect: picker behaves exactly as before — filters,
   "Selected only", basket, pagination, prompt-per-kind, start-batch
   flow all intact, including selection surviving paging and the
   pre-seeded open from the clips list (`?new=1` / sessionStorage
   seed).
