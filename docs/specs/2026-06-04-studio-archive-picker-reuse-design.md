# Studio archive picker — reuse the batch picker list

**Date:** 2026-06-04
**Status:** Approved

## Problem

There are two clip-picker lists in the app:

- The **New-batch picker** (`batches.html` + `GET /batches/picker`)
  renders rich rows through the shared `_video_list.html` scaffold:
  select-all + per-row checkboxes, cache badge, thumbnail + name,
  year / duration / type cells, server pagination, text search.
- The **Studio archive picker** (`_studio_archive_picker.html` +
  `GET /studio/_archive_picker`) renders bare `picker-row` labels:
  checkbox, name, `id:42`. No thumbnails, no cache badges, no
  pagination (hard 50 cap), no metadata.

The studio one is a significantly worse parallel renderer of the same
concept. Per the project's reuse discipline (CLAUDE.md "Frontend:
explore before implementing"), the duplicate must go: the studio modal
should render its results through the same list the batch picker uses.

## Goals

- One renderer and one route for "a pageable, searchable, selectable
  list of catalog clips": the existing `/batches/picker` endpoint and
  the `_video_list.html`-based `_batch_picker.html` partial.
- The studio archive picker keeps its job (multi-select clips, add to
  a studio folder) but gains the rich rows, server pagination, and the
  same search behaviour.
- The bare `picker-row` markup, its now-dead `.picker-row` CSS rules
  in `app.css`, and the studio-side list-rendering route branch are
  **deleted**.

## Non-goals

- No cache-state / annotation-status filter dropdowns in the studio
  modal (batch-workflow extras).
- No "Selected only" toggle or selected-clips basket sidebar.
- No changes to the batch picker UX. `batches.py` changes are limited
  to a docstring note that the endpoint is shared.
- No extraction of a fully parameterized shared `clipPicker` Alpine
  component (that would mean rewriting the working batch picker —
  out of scope).

## Locked decisions

- **Approach A** (of three considered): the studio modal shell stays a
  studio partial; its results region fetches `/batches/picker`
  directly. Rejected: (B) studio route renders the shared partial
  itself — duplicates ~20 lines of route plumbing; (C) full shared
  component extraction — a refactor, not a tweak.
- Offline behaviour changes deliberately: `/batches/picker` needs live
  services (typed 503), so the studio modal now shows a clear
  "catalog unavailable" message instead of a silently empty list.
  This is an improvement, not a regression.

## Design

### `_studio_archive_picker.html` (modal shell only)

- Keep the modal chrome: header, backdrop, Cancel/Add footer
  (`picked.size + ' selected'`, Add → existing
  `POST /api/studio/folders/{id}/clips`).
- The search `<input>` drops its HTMX attributes; it binds to the
  Alpine component (`x-model` + debounced `fetchPage()`), matching the
  batch modal's pattern.
- `.modal-results` becomes an empty target the component fills.
- A pager row (Prev / label / Next) sits under the results, using the
  same `nb-pager` markup/classes as the batch modal so the CSS is
  reused as-is.

### `studio.js` — `archivePicker` component (~25 new lines)

New state: `q`, `offset`, `limit: 15`, `total`.

New methods, mirroring `batchesPage` but scoped to the modal element
(no document-level listener):

- `fetchPage()` — `fetch('/batches/picker?' + params)`, inject HTML
  into `.modal-results`, `window.htmxAlpine.reinit(...)`, read
  `#nb-list-meta` for `total`, re-apply `picked` to `.row-check`
  checkboxes (values are `catdv/{id}`; parse the int id). On `!r.ok`
  render the error detail in the results region and push an error
  toast (`Alpine.store('toast')`).
- `goPage(d)` / `pagerLabel()` — same arithmetic as the batch modal.
- A `@change` handler on the results container syncs `.row-check`
  and `#row-select-all` into the `picked` Set.
- `init()` fetches page 1.

Unchanged: `toggle(id)`, `addSelected()`, `close()`.

### `routes/pages/studio.py` — `_studio_archive_picker`

Drops the `archive.list_clips` call and the `results` / `q` context;
renders only the shell (`folder_id`). The `ClipQuery` import and the
archive lookup in this handler go away.

### `routes/batches.py` — `batches_picker`

Docstring gains one line: also serves the Studio archive-picker modal.

## Testing strategy

TDD throughout (failing test first):

1. **Route test:** `GET /studio/_archive_picker?folder_id=N` renders
   the shell — asserts the modal markup is present and the bare
   `picker-row` string is **absent**; no archive call is made.
2. **Reuse guard:** the string `picker-row` no longer appears in any
   template or static asset (grep-style unit test, same spirit as the
   existing pattern-guard tests).
3. **Existing coverage stays green:** studio folder add-clips tests,
   `/batches/picker` tests.

## Manual acceptance flows

1. **Add clips via the rich picker.** Setup: app running with live
   CatDV, a studio folder exists. Open `/studio`, expand a folder,
   click "+ Add from archive". Expect: modal opens and the results
   show rich rows — thumbnail, cache badge, clip name, year, duration,
   type — with a select-all header checkbox, identical in look to the
   New-batch picker rows. Tick two clips, click "Add". Expect: modal
   closes, success toast, the two clips appear in the folder.
2. **Search + pagination.** In the same modal, type a query — results
   re-fetch after a pause; clear it, page with Next/Prev — the
   range label updates, and a clip ticked on page 1 stays ticked when
   you return to page 1.
3. **Selection across pages.** Tick one clip on page 1 and one on
   page 2; footer reads "2 selected"; Add inserts both into the
   folder.
4. **Offline behaviour.** With CatDV offline (no LiveCtx), open the
   picker. Expect: modal still opens; results region shows a clear
   "catalog unavailable"-style message (not a silent empty list); an
   error toast appears; Cancel still closes the modal.
5. **Batch picker regression check.** Open `/batches`, click
   "+ New batch". Expect: picker behaves exactly as before — filters,
   basket, pagination, start-batch flow all intact.
