# Shared clip search/filter component (clips list ↔ studio picker) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** Deferred — captured for later. Filed as a GitHub issue.

**Goal:** The studio "Add clips to folder" picker reuses the search + cache/anno filters + cache-badge results table from the main `/clips` list page. The filter chrome and the list+filter view-model become shared components used by both pages.

**Architecture:** Two extractions (one Jinja partial for the filter form, one Python helper for the filter+page lookup) plus one parameter on the existing `_video_list.html` partial. The clips page route shrinks to a thin caller; the studio picker route adopts the same caller pattern. No new DB schema, no migrations.

**Tech stack:** Python 3.13, FastAPI, Jinja2, Alpine.js v3, HTMX, pytest, ruff, basedpyright.

**Spec drivers:**
- The `/clips` page already filters by `q` + `cache` (`any|none|local|ai`) + `anno` (`any|for_review|applied|none|has_any`); filter logic lives in `services/clip_list_filters.py`.
- The shared list partial `_video_list.html` already handles thumbnails, cache badges, and the select-all + per-row checkbox pattern.
- The current studio picker (`_studio_archive_picker.html` → `routes/pages/studio.py::_studio_archive_picker`) only has a bare `q` search and a flat checkbox list — no filters, no thumbnails, no cache badges, no pagination.

This plan codifies the work surfaced during PR2 manual testing where the studio picker quality was visibly behind the main clips page.

---

## What we're shipping

| Concern | Where today | After |
|---|---|---|
| Search input + cache + anno selects + Reset button | inlined into `pages/clips.html` lines ~13-105 | extracted into `pages/_clip_filter_form.html`; both pages include it |
| Filter resolution + paginated fetch | inlined into `routes/pages/clips.py::index_page` lines 76-135 | extracted into `services/clip_picker.py::filtered_clips_page()`; both routes call it |
| Result table (thumb + badge + name + per-page cells) | `pages/_video_list.html` (already shared) | unchanged behavior; gains one optional `row_check_class` param |
| Studio picker UI | bespoke `_studio_archive_picker.html` (search + flat list) | rebuilt around the two extractions; rows render via `_video_list.html` with picker-specific row/head cells |

After the work:
- Adding a filter option (e.g. `kind=image|video`) is a one-line addition to the shared partial + helper.
- Visual fixes to clip rendering land in both places automatically.
- The studio picker gets thumbnails + cache badges + pagination for free.

---

## File map

**Create:**

- `backend/app/templates/pages/_clip_filter_form.html` — extracted filter chrome (search input + cache select + anno select + Reset)
- `backend/app/templates/pages/_picker_head_cells.html` — picker table head (e.g. just `Duration`)
- `backend/app/templates/pages/_picker_row_cells.html` — picker table row cells (duration; checkbox bound to `archivePicker.picked` Set via Alpine)
- `backend/app/services/clip_picker.py` — `FilteredPage` dataclass + `filtered_clips_page()` helper
- `tests/unit/test_clip_picker_helper.py` — pure unit tests of the helper with mocked ctx
- `tests/integration/test_studio_picker_filters.py` — integration: drives `/studio/_archive_picker?q=&cache=&anno=&offset=&limit=` and asserts shape

**Modify:**

- `backend/app/templates/pages/clips.html` — replace inline `<form class="filter-form">` block (~lines 13-105) with `{% include "pages/_clip_filter_form.html" %}`; preserve the bulk-Actions dropdown alongside
- `backend/app/templates/pages/_video_list.html` — add optional `row_check_class` param (default `"row-check"`)
- `backend/app/templates/pages/_clips_tbody.html` — explicitly pass `row_check_class = "row-check"` if needed (or rely on default — verify)
- `backend/app/templates/pages/_studio_archive_picker.html` — rewrite to use shared filter form + `_video_list.html` + picker row/head cells
- `backend/app/routes/pages/clips.py::index_page` — replace inline filter+fetch with `filtered_clips_page()` call (pure refactor; existing tests must stay green)
- `backend/app/routes/pages/studio.py::_studio_archive_picker` — accept `cache`, `anno`, `offset`, `limit` query params; call `filtered_clips_page()`; pass `host_local_proxies`
- `backend/app/static/app.css` — add `.modal-card--wide` for the roomier picker layout

**Maybe modify (judgment call during execution):**

- `backend/app/static/studio.js` — if `bulkSel()` from clips.html document-listener conflicts with picker checkboxes, scope its listener to `#clips-region` (or have picker emit a different class — see Task 1)

---

## Task breakdown

Five atomic tasks. Each is TDD: red test → minimal impl → green test → commit.

### Task 1: Parameterize `_video_list.html` row-check class

**Files:**
- Modify: `backend/app/templates/pages/_video_list.html` (line 36 hardcodes `class="row-check"`)
- Tests: existing `tests/integration/test_clip_list_*.py` should still pass without modification (default preserved)

- [ ] **Step 1:** Read `_video_list.html` to confirm the exact hardcoded class. Add a `{% set row_check_class = row_check_class | default("row-check") %}` at the top, change the input to `class="{{ row_check_class }}"`.

- [ ] **Step 2:** Run the existing clips list integration test suite. Expected: same green count as before.

  ```bash
  .venv/bin/pytest -q tests/integration/test_clip_list_*.py
  ```

- [ ] **Step 3:** Commit.

  ```bash
  git add backend/app/templates/pages/_video_list.html
  git commit -m "refactor(video-list): parameterize row-check class (default unchanged)"
  ```

### Task 2: Extract `filtered_clips_page()` helper

**Files:**
- Create: `backend/app/services/clip_picker.py`
- Create: `tests/unit/test_clip_picker_helper.py`

- [ ] **Step 1:** Read `routes/pages/clips.py` lines 76-135 (the inline filter+fetch logic). Inventory:
  - Inputs: `q`, `cache_filter`, `anno_filter`, `offset`, `limit`, `catalog_id`, `ctx` (for `archive`, `db`, `proxy_cache_repo`, etc.).
  - Behavior: when filters active, local-first via `resolve_filters` + page slice; when no filters, `archive.list_clips(catalog_id, ClipQuery(text=q, offset, limit))`.
  - Outputs: `clips` (list), `total` (int), plus `cache_status_view` enrichment.
  - `mode` ("online" vs "cache-only") for banner rendering.

- [ ] **Step 2:** Write the failing helper tests. Test cases:
  - Empty filters → archive `list_clips` called once, helper returns its results.
  - Cache filter active → `resolve_filters` called, archive NOT called (local-first path), results restricted to candidate IDs.
  - Empty candidate set (filters too restrictive) → returns `clips=[]`, `total=0`.

- [ ] **Step 3:** Run tests, expect FAIL (`ModuleNotFoundError`).

- [ ] **Step 4:** Implement the helper.

  ```python
  # backend/app/services/clip_picker.py
  from dataclasses import dataclass
  from typing import Literal

  from backend.app.archive.model import CanonicalClip, ClipQuery
  from backend.app.services.clip_list_filters import (
      AnnoFilter, CacheFilter, is_active, normalize_anno, normalize_cache,
      resolve as resolve_filters,
  )

  @dataclass
  class FilteredPage:
      clips: list[CanonicalClip]
      total: int
      q: str
      cache_filter: CacheFilter
      anno_filter: AnnoFilter
      offset: int
      limit: int
      mode: Literal["online", "cache-only"]

  async def filtered_clips_page(
      ctx, *, q: str, cache_filter: str, anno_filter: str,
      offset: int, limit: int, catalog_id: str,
  ) -> FilteredPage:
      # ... port the inline logic from routes/pages/clips.py:76-135 verbatim,
      # parameterized on inputs. Both filter-active and filter-inactive paths.
  ```

- [ ] **Step 5:** Run helper tests. Expected: PASS.

- [ ] **Step 6:** Commit.

  ```bash
  git add backend/app/services/clip_picker.py tests/unit/test_clip_picker_helper.py
  git commit -m "feat(services): filtered_clips_page helper — shared filter+fetch logic"
  ```

### Task 3: Refactor `clips.py` route to call the helper

**Files:**
- Modify: `backend/app/routes/pages/clips.py::index_page`

- [ ] **Step 1:** Snapshot the current behavior: run `pytest -q tests/integration/test_clip_list_*.py` and record the green count.

- [ ] **Step 2:** Replace the inline filter+fetch block in `index_page` with a single call to `filtered_clips_page()`. The template context shape stays identical.

- [ ] **Step 3:** Run the same tests. Same green count expected — pure refactor.

  ```bash
  .venv/bin/pytest -q tests/integration/test_clip_list_*.py
  ```

- [ ] **Step 4:** Commit.

  ```bash
  git add backend/app/routes/pages/clips.py
  git commit -m "refactor(clips): consume filtered_clips_page helper (no behavior change)"
  ```

### Task 4: Extract `_clip_filter_form.html` partial

**Files:**
- Create: `backend/app/templates/pages/_clip_filter_form.html`
- Modify: `backend/app/templates/pages/clips.html`

- [ ] **Step 1:** Cut lines ~13-105 of `clips.html` (the `<form class="filter-form">` block, excluding the bulk Actions dropdown) into `_clip_filter_form.html`. Parameterize:
  - `action` (form `action` + `hx-get`)
  - `hx_target` (defaults to `#clips-region`)
  - `q`, `cache_filter`, `anno_filter` (sticky values)
  - `host_local_proxies` (hides cache select in fs-mode)
  - `push_url` (boolean; defaults to True for clips page, False for picker)
  - `omit_anno_filter` (optional bool; default False)

- [ ] **Step 2:** In `clips.html`, replace the cut block with `{% include "pages/_clip_filter_form.html" %}` and keep the bulk Actions dropdown immediately after.

- [ ] **Step 3:** Verify visually:

  ```bash
  curl -sS http://127.0.0.1:8765/ | grep -E "filter-form|name=\"q\"|name=\"cache\"|name=\"anno\"" | head
  ```

  Expected: same markup as before the refactor (compare against pre-refactor snapshot).

- [ ] **Step 4:** Run existing clips list tests. All green.

- [ ] **Step 5:** Commit.

  ```bash
  git add backend/app/templates/pages/_clip_filter_form.html backend/app/templates/pages/clips.html
  git commit -m "refactor(clips): extract _clip_filter_form.html for cross-page reuse"
  ```

### Task 5: Studio picker adopts shared form + table

**Files:**
- Modify: `backend/app/templates/pages/_studio_archive_picker.html`
- Modify: `backend/app/routes/pages/studio.py::_studio_archive_picker`
- Create: `backend/app/templates/pages/_picker_head_cells.html`
- Create: `backend/app/templates/pages/_picker_row_cells.html`
- Modify: `backend/app/static/app.css` (`.modal-card--wide`)
- Create: `tests/integration/test_studio_picker_filters.py`

- [ ] **Step 1:** Write integration tests for the picker's new query surface.

  Cases:
  - GET `/studio/_archive_picker?folder_id=1` → 200, response contains the filter form selects and `_video_list.html` table.
  - GET `/studio/_archive_picker?folder_id=1&q=foo&cache=local&anno=for_review&limit=10` → query params honored.
  - Picker rows render the picker-specific checkbox class (NOT the document-listener `row-check`), so they don't trigger `bulkSel()` from clips.

- [ ] **Step 2:** Run tests, expect FAIL.

- [ ] **Step 3:** Rewrite `_studio_archive_picker.html`:

  ```jinja
  {# Studio archive picker modal. Search + filter + multi-select clips
     into a folder. Reuses the shared filter form and video list. #}
  <div class="modal" x-data="archivePicker({{ folder_id }})" @keydown.escape.window="close()">
    <div class="modal-backdrop" @click="close()"></div>
    <div class="modal-card modal-card--wide">
      <div class="modal-hdr">
        <h2>Add clips to folder</h2>
        <span class="grow"></span>
        <button class="btn ghost" @click="close()">×</button>
      </div>
      <div class="modal-body">
        {% with action = '/studio/_archive_picker?folder_id=' ~ folder_id|string,
                hx_target = '#picker-results',
                push_url = False,
                host_local_proxies = host_local_proxies,
                q = q, cache_filter = cache_filter, anno_filter = anno_filter %}
          {% include "pages/_clip_filter_form.html" %}
        {% endwith %}
        <div id="picker-results" class="modal-results">
          {% with rows = clips,
                  head_cells = "pages/_picker_head_cells.html",
                  row_cells = "pages/_picker_row_cells.html",
                  row_check_class = "picker-check",
                  colspan = 4,
                  empty_msg = "No clips match." %}
            {% include "pages/_video_list.html" %}
          {% endwith %}
          {# Optional: pager — re-use existing _pager.html for consistency. #}
        </div>
      </div>
      <div class="modal-foot">
        <span class="muted" x-text="picked.size + ' selected'"></span>
        <span class="grow"></span>
        <button class="btn ghost" @click="close()">Cancel</button>
        <button class="btn primary" @click="addSelected()" :disabled="picked.size === 0">Add</button>
      </div>
    </div>
  </div>
  ```

- [ ] **Step 4:** Create `_picker_head_cells.html` (e.g. just `<th class="col-duration">Duration</th>`) and `_picker_row_cells.html`:

  ```jinja
  <td class="cell-duration mono">{{ row.duration_smpte if row.duration_smpte else '—' }}</td>
  ```

- [ ] **Step 5:** Update the studio picker route:

  ```python
  @router.get("/studio/_archive_picker", response_class=HTMLResponse)
  async def _studio_archive_picker(
      request: Request,
      folder_id: int,
      q: str = "",
      cache: str = "any",
      anno: str = "any",
      offset: int = 0,
      limit: int = 25,
  ):
      ctx = get_ctx(request)
      page = await filtered_clips_page(
          ctx, q=q, cache_filter=cache, anno_filter=anno,
          offset=offset, limit=limit,
          catalog_id=str(ctx.settings.catdv_catalog_id),
      )
      return templates.TemplateResponse(
          request, "pages/_studio_archive_picker.html",
          {
              "folder_id": folder_id,
              "clips": page.clips,
              "total": page.total,
              "q": page.q,
              "cache_filter": page.cache_filter,
              "anno_filter": page.anno_filter,
              "offset": page.offset,
              "limit": page.limit,
              "mode": page.mode,
              "host_local_proxies": getattr(ctx.proxy_resolver, "is_host_local", False),
          },
      )
  ```

- [ ] **Step 6:** Add `.modal-card--wide` CSS (e.g. `max-width: 960px` vs the default modal width) plus `.picker-check` styles if needed.

- [ ] **Step 7:** Run all new + existing tests:

  ```bash
  .venv/bin/pytest -q tests/integration/test_studio_picker_filters.py \
                      tests/integration/test_studio_api.py \
                      tests/integration/test_clip_list_*.py
  ```

  All green.

- [ ] **Step 8:** Manual smoke (browser):

  1. Open `/studio`, expand a folder, click `+ Add from archive`.
  2. Filter strip renders identical to `/`: search, cache select, anno select, Reset.
  3. Pick filters → results table updates via HTMX (modal stays open).
  4. Multi-select clips → `Add` adds them to the folder, modal closes.
  5. Verify `/clips` page still works identically (regression check).

- [ ] **Step 9:** Commit.

  ```bash
  git add backend/app/templates/pages/_studio_archive_picker.html \
          backend/app/templates/pages/_picker_head_cells.html \
          backend/app/templates/pages/_picker_row_cells.html \
          backend/app/routes/pages/studio.py \
          backend/app/static/app.css \
          tests/integration/test_studio_picker_filters.py
  git commit -m "feat(studio): picker reuses /clips filter form + video-list table"
  ```

---

## Manual acceptance flows

Each numbered flow corresponds to one capability the work introduces. Walk through after Task 5 commits.

1. **Picker shows filter chrome.** `/studio` → folder → `+ Add from archive`. Modal includes the search input, Cache select (`Any/None/Local/AI`), Annotations select, and a Reset link — visually identical to `/`.

2. **Filtering inside the picker works locally.** Pick `Cache = Local`. The modal updates (no full reload) to show only locally-cached clips. The number of results changes; pagination updates.

3. **Search inside the picker works.** Type `praha` (or whatever matches your dataset). Modal updates within ~300ms (HTMX debounced).

4. **Reset clears picker state.** Click Reset. Filter selects go back to `Any`, search clears, all results return.

5. **Multi-select + Add still works.** Tick 3 clips, click `Add`. Modal closes; the folder list refreshes with those 3 clips included.

6. **Picker open is clean.** Close the modal, reopen. Filters are reset to defaults (the picker does not persist state across opens — clips page does, but picker shouldn't).

7. **`/clips` page unchanged.** Navigate to `/`. Same filter behavior as before this work landed. Search, filters, pagination, bulk Actions dropdown all functional.

8. **No double-trigger on select-all.** On `/clips`, click the select-all checkbox at the top of the table — only clips-page checkboxes toggle. Open the picker — its checkboxes are independent of the document-listener `bulkSel()` scope.

---

## Risks & mitigations

- **The `clips.py` route refactor must be behaviorally identical.** Mitigation: don't change `clips.html`'s template context shape. Run the existing `tests/integration/test_clip_list_*.py` suite before AND after Task 3.
- **`_video_list.html` is the most-shared partial.** Parameterizing `row_check_class` is one line with default-preserved. Other callers (cache page, prompts page, etc.) keep working unchanged because the default matches.
- **`bulkSel()` document-listener vs picker checkboxes.** Picker uses class `picker-check` (different from `row-check`) so `bulkSel()` never sees picker checkboxes. The bulk Actions dropdown stays on `/clips` only.
- **Picker modal is wider than the existing default.** New `.modal-card--wide` CSS rule isolates this; doesn't affect the existing folder-name / rename modal sizes.
- **Cache filter against archive-unlisted clips.** If `cache=ai` returns clip IDs the archive can't enrich, the picker might show empty rows. Same risk on `/clips` today — defer; revisit if it bites.

## Open questions

None blocking. Possible follow-ups:

- **Per-prompt or per-folder smart filters** (e.g. "show only clips that haven't been run with this version yet"). Out of scope; would build on top of this shared component.
- **Drag-to-reorder folder contents.** Out of scope; orthogonal to picker.
- **Sticky picker filter state across opens.** Explicit no for v1 — picker is transient. If users ask for it, sessionStorage namespaced per folder.

## Out of scope

- Bulk Actions dropdown (Cache locally / Remove from local cache) inside the picker. Stays on `/clips` only.
- `bulkSel()` select-all checkbox inside the picker. Picker uses per-row Alpine `picked: Set`.
- New filter dimensions (`kind=image|video`, `year_min/max`, etc.). Plan supports adding them later in `_clip_filter_form.html` + `clip_list_filters.py` without rework.
