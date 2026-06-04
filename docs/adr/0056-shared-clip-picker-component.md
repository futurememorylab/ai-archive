# 0056. Shared clip-picker component (studio archive picker reuses the batch picker)

**Date:** 2026-06-04
**Status:** Accepted

## Context

The studio archive-picker modal ("+ Add from archive" on a studio
folder) rendered its own bare clip rows — checkbox, name, `id:42` —
via `GET /studio/_archive_picker`, while the New-batch picker rendered
rich rows (thumbnail, cache badge, year/duration/type, select-all,
pagination, filters, basket) through `GET /batches/picker` +
`_video_list.html`. Two parallel renderers of the same concept, one
markedly worse.

The work landed in two scopes on the same day:

- **v1:** studio modal keeps its shell but fetches result pages from
  the shared `/batches/picker` endpoint (list + pager + search only).
- **v2 (user request, same session):** the studio modal should also
  get the cache/anno filters, the "Selected only" toggle, and the
  selected-clips basket — i.e. the full picker UX.

## Alternatives

1. **Studio route renders the shared partial itself** — duplicates
   ~20 lines of route plumbing from `batches.py` for no benefit.
2. **Copy the batch picker's filter/basket markup + JS into studio**
   (~150 lines JS + ~40 lines markup) — recreates the original
   duplication one level up; rejected outright ("do not duplicate
   code, repurpose").
3. **Extract the picker into a shared component used by both pages**
   — requires rewiring the *working* batch picker. Rejected in v1
   when the studio delta was small ("a refactor, not a tweak");
   chosen in v2 once the delta became the whole picker.

## Decision

- One picker. `window.clipPickerCore()` (`static/clipPicker.js`)
  owns the picker state + logic (search/filter state, `sel` map of
  `{id, name, kind, thumb}`, `fetchPage` → `/batches/picker`, pager,
  checkbox sync, selected-only rendering, basket helpers). Pages
  compose it by object spread into their Alpine component:
  `batchesPage()` adds the jobs table + prompt-per-kind + startBatch;
  `archivePicker(folderId)` adds `init`/`addSelected`/`close`.
- One markup source. `pages/_clip_picker_main.html` (filters + list +
  pager) and `pages/_clip_picker_basket.html` (selected sidebar) are
  included by both modals; each page keeps its own modal chrome and
  footer action.
- `/batches/picker` is the single row-rendering endpoint for pickable
  clip lists (docstring records the dual consumer).
- Deliberate behavior changes while extracting:
  - The batch picker's document-level checkbox `change` listener
    (gated on `newOpen`) became a container-level `@change` on
    `.nb-list` — same semantics, properly scoped, shareable.
  - `getElementById("nb-table")` became
    `$root.querySelector('.nb-list')` — component-scoped, no id
    coupling.
  - The non-OK fetch error detail is now HTML-escaped before
    `innerHTML` injection (was unescaped in both old copies).
  - Studio offline behavior: `/batches/picker` needs live services
    (typed 503), so the studio modal now shows an escaped
    "catalog unavailable" message + error toast instead of the old
    silently-empty list.

## Consequences

- The studio picker gained thumbnails, cache badges, metadata
  columns, server pagination, filters, selected-only, and the basket
  for ~45 lines of page-specific JS.
- Guard tests enforce the single-definition contract:
  `tests/unit/test_clip_picker_single_definition.py` (core methods
  exist only in `clipPicker.js`), `tests/unit/test_no_picker_row.py`
  (the bare renderer cannot return), and the updated
  `tests/unit/test_studio_archive_picker_js.py` (`archivePicker`
  spreads the core).
- Any future change to picker behavior lands in one file and shows
  up in both modals; conversely, batch-picker changes now affect the
  studio modal — the batches integration suite and the studio shell
  test together pin both consumers.
- The shared partials bind core state names (`q`, `cacheF`, `annoF`,
  `selOnly`, `offset`, `perPage`, `total`); a third consumer must
  spread `clipPickerCore()` rather than re-implementing.
