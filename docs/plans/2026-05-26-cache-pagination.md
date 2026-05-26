# Cache Page Pagination (shared with Clips) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Paginate the Cache page inventory (50/page, Prev/Next via HTMX) reusing a shared pager partial and offset helper with the Clips list.

**Architecture:** Extract the offset math into `backend/app/ui/pagination.py::page_offsets` and the pager markup into `pages/_pager.html` (parameterized: full-nav links for Clips, `hx-get` for Cache). The Cache route slices its already-in-memory `rows_for_template` list and renders the shared pager into the HTMX-swapped `#cache-table-region`. No server/API-level paging — the cache inventory is fully assembled in memory, so slicing is correct.

**Tech Stack:** FastAPI + Jinja2, htmx 1.9.10, pytest. Lint/type gate: `ruff`, `basedpyright` (baseline), `import-linter`, `interrogate` (module docstrings required on new backend files; `tests/` excluded).

**Spec:** `docs/specs/2026-05-26-cache-pagination-design.md`

---

## File Structure

- `backend/app/ui/pagination.py` — NEW. `page_offsets(offset, limit, total)` returning `(prev_offset, next_offset)`. One responsibility: pager offset math.
- `backend/app/templates/pages/_pager.html` — NEW. Shared `<nav class="pager">` markup; full-nav or `hx-get` depending on an optional `hx_target`.
- `backend/app/routes/pages/clips.py` — MODIFY. Use `page_offsets` instead of inline math.
- `backend/app/templates/pages/_clips_tbody.html` — MODIFY. Include `_pager.html` instead of inline `<nav>`.
- `backend/app/routes/cache.py` — MODIFY. Add `offset`/`limit`, slice rows, add pager fields to ctx.
- `backend/app/templates/pages/_cache_inventory_table.html` — MODIFY. Include `_pager.html` (hx).
- `backend/app/templates/cache_page.html` — MODIFY. Entries count shows `total`.
- `tests/unit/test_pagination.py` — NEW. Unit tests for `page_offsets`.
- `tests/integration/test_routes_cache.py` — MODIFY. Pagination route tests.

---

## Task 1: `page_offsets` helper + refactor Clips route

**Files:**
- Create: `backend/app/ui/pagination.py`
- Create: `tests/unit/test_pagination.py`
- Modify: `backend/app/routes/pages/clips.py`

- [ ] **Step 1: Write the failing unit test**

Create `tests/unit/test_pagination.py`:

```python
from backend.app.ui.pagination import page_offsets


def test_first_page_has_no_prev():
    assert page_offsets(offset=0, limit=50, total=240) == (None, 50)


def test_middle_page_has_both():
    assert page_offsets(offset=50, limit=50, total=240) == (0, 100)


def test_last_page_has_no_next():
    assert page_offsets(offset=200, limit=50, total=240) == (150, None)


def test_single_page_has_neither():
    assert page_offsets(offset=0, limit=50, total=30) == (None, None)


def test_empty_has_neither():
    assert page_offsets(offset=0, limit=50, total=0) == (None, None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_pagination.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.app.ui.pagination'`.

- [ ] **Step 3: Create the helper**

Create `backend/app/ui/pagination.py`:

```python
"""Pager offset math shared by the clips and cache list pages."""

from __future__ import annotations


def page_offsets(offset: int, limit: int, total: int) -> tuple[int | None, int | None]:
    """Return (prev_offset, next_offset) for a paged list, or None at an edge."""
    prev_offset = max(0, offset - limit) if offset > 0 else None
    next_offset = offset + limit if offset + limit < total else None
    return prev_offset, next_offset
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_pagination.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Refactor `clips.py` to use the helper**

In `backend/app/routes/pages/clips.py`, add the import near the other `backend.app` imports at the top of the file:

```python
from backend.app.ui.pagination import page_offsets
```

Then in `clips_list`, find these two lines inside the `ctx_dict` dict literal:

```python
        "prev_offset": max(0, offset - limit) if offset > 0 else None,
        "next_offset": offset + limit if offset + limit < total else None,
```

Replace them with:

```python
        "prev_offset": prev_offset,
        "next_offset": next_offset,
```

And immediately **before** the `ctx_dict = {` line, add:

```python
    prev_offset, next_offset = page_offsets(offset, limit, total)
```

- [ ] **Step 6: Verify Clips still works (no behaviour change)**

Run: `.venv/bin/pytest tests/integration/test_routes_pages.py -q`
Expected: PASS (clips page tests unchanged).

- [ ] **Step 7: Commit**

```bash
git add backend/app/ui/pagination.py tests/unit/test_pagination.py backend/app/routes/pages/clips.py
git commit -m "refactor(ui): extract page_offsets helper, use it in clips route"
```

---

## Task 2: Shared `_pager.html` partial + refactor Clips template

**Files:**
- Create: `backend/app/templates/pages/_pager.html`
- Modify: `backend/app/templates/pages/_clips_tbody.html`

- [ ] **Step 1: Create the shared pager partial**

Create `backend/app/templates/pages/_pager.html`:

```jinja
{# Shared list pager. Inputs (via {% with %}):
     prev_url / next_url  (str|None)  built hrefs, None = disabled edge
     range_label          (str)       e.g. "1–50 of 240" or "0 of 0"
     hx_target            (str|None)  set => hx-get into that target (Cache);
                                      unset/None => plain href full-nav (Clips) #}
<nav class="pager">
  {% if prev_url %}
    <a class="pg-btn"{% if hx_target %} hx-get="{{ prev_url }}" hx-target="{{ hx_target }}" hx-swap="innerHTML" hx-push-url="true"{% else %} href="{{ prev_url }}"{% endif %}>‹ Prev</a>
  {% else %}
    <span class="pg-btn disabled">‹ Prev</span>
  {% endif %}
  <span class="pg-meta mono">{{ range_label }}</span>
  {% if next_url %}
    <a class="pg-btn"{% if hx_target %} hx-get="{{ next_url }}" hx-target="{{ hx_target }}" hx-swap="innerHTML" hx-push-url="true"{% else %} href="{{ next_url }}"{% endif %}>Next ›</a>
  {% else %}
    <span class="pg-btn disabled">Next ›</span>
  {% endif %}
</nav>
```

- [ ] **Step 2: Refactor `_clips_tbody.html` to include it**

In `backend/app/templates/pages/_clips_tbody.html`, replace the entire block from line 12 (`{% set _pq ... %}`) through the closing `</nav>` (currently lines 12–29) with:

```jinja
  {% set _pq = 'q=' ~ (q|urlencode) ~ '&limit=' ~ limit %}
  {% if cache_filter and cache_filter != 'any' %}{% set _pq = _pq ~ '&cache=' ~ cache_filter %}{% endif %}
  {% if anno_filter and anno_filter != 'any' %}{% set _pq = _pq ~ '&anno=' ~ anno_filter %}{% endif %}
  {% set _range = ((offset + 1) ~ '–' ~ (offset + clips|length) ~ ' of ' ~ total) if clips else ('0 of ' ~ total) %}
  {% with prev_url = ('/?' ~ _pq ~ '&offset=' ~ prev_offset) if prev_offset is not none else none,
          next_url = ('/?' ~ _pq ~ '&offset=' ~ next_offset) if next_offset is not none else none,
          range_label = _range,
          hx_target = none %}
    {% include "pages/_pager.html" %}
  {% endwith %}
```

Leave the surrounding `<div id="clips-region">` / `.tbl-scroll` / `_video_list` block (lines 1–11) and the final `</div>` (line 30) intact.

- [ ] **Step 3: Verify Clips render is unchanged**

Run: `.venv/bin/pytest tests/integration/test_routes_pages.py -q`
Expected: PASS. (If a test asserts exact pager whitespace and trips on it, that's a real diff to inspect — the visible text `‹ Prev`, range, `Next ›` and classes must be unchanged.)

Then sanity-check the rendered pager markup directly:

Run: `.venv/bin/python -c "from fastapi.templating import Jinja2Templates"`
Expected: no error (templates import cleanly).

- [ ] **Step 4: Commit**

```bash
git add backend/app/templates/pages/_pager.html backend/app/templates/pages/_clips_tbody.html
git commit -m "refactor(ui): extract shared _pager.html partial, use it in clips list"
```

---

## Task 3: Cache pagination — route + templates + tests

**Files:**
- Modify: `backend/app/routes/cache.py`
- Modify: `backend/app/templates/pages/_cache_inventory_table.html`
- Modify: `backend/app/templates/cache_page.html`
- Modify: `tests/integration/test_routes_cache.py`

- [ ] **Step 1: Write the failing route tests**

Append to `tests/integration/test_routes_cache.py`:

```python
def test_cache_pagination_first_page(monkeypatch, tmp_path: Path):
    """limit=2 over 3 clips shows 2 rows + a next link to offset=2."""
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        _seed_clip(client, key=("catdv", "5001"))
        _seed_clip(client, key=("catdv", "5002"))
        _seed_clip(client, key=("catdv", "5003"))
        r = client.get("/cache?tab=all&limit=2", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert r.text.count('class="row-check"') == 2
    assert "offset=2" in r.text  # next link
    assert "of 3" in r.text  # "1–2 of 3" range label


def test_cache_pagination_second_page(monkeypatch, tmp_path: Path):
    """offset=2&limit=2 over 3 clips shows the remaining 1 row + a prev link."""
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        _seed_clip(client, key=("catdv", "5001"))
        _seed_clip(client, key=("catdv", "5002"))
        _seed_clip(client, key=("catdv", "5003"))
        r = client.get(
            "/cache?tab=all&limit=2&offset=2", headers={"HX-Request": "true"}
        )
    assert r.status_code == 200
    assert r.text.count('class="row-check"') == 1
    assert "offset=0" in r.text  # prev link


def test_cache_queue_tab_has_no_pager(monkeypatch, tmp_path: Path):
    """Queue tab is a live list, not paginated."""
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        r = client.get("/cache?tab=queue", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert 'class="pager"' not in r.text
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/integration/test_routes_cache.py -k pagination -v`
Expected: FAIL — without slicing, the first-page test sees 3 `row-check`s (not 2) and no `offset=2` link.

- [ ] **Step 3: Add pagination to the cache route**

In `backend/app/routes/cache.py`, add the import near the existing `backend.app` imports at the top:

```python
from backend.app.ui.pagination import page_offsets
```

Add `offset` and `limit` parameters to `cache_page`. Change the signature from:

```python
@page_router.get("/cache", response_class=HTMLResponse)
async def cache_page(
    request: Request,
    tab: str | None = None,
    store: str | None = None,
    workspace: int | None = None,
    orphans: int | None = None,
    evictable: int | None = None,
) -> HTMLResponse:
```

to:

```python
@page_router.get("/cache", response_class=HTMLResponse)
async def cache_page(
    request: Request,
    tab: str | None = None,
    store: str | None = None,
    workspace: int | None = None,
    orphans: int | None = None,
    evictable: int | None = None,
    offset: int = 0,
    limit: int = 50,
) -> HTMLResponse:
```

Then find the line `summary = await insp.summary()` (it comes right after the `rows_for_template = [_cache_row(s) for s in rows]` block / the queue-tab `else`). Immediately **before** `summary = await insp.summary()`, insert:

```python
    total = len(rows_for_template)
    page_rows = rows_for_template[offset : offset + limit]
    prev_offset, next_offset = page_offsets(offset, limit, total)
```

Then in the `ctx_dict = {` literal, change the rows line from:

```python
        "rows": rows_for_template,
```

to:

```python
        "rows": page_rows,
        "offset": offset,
        "limit": limit,
        "total": total,
        "prev_offset": prev_offset,
        "next_offset": next_offset,
```

- [ ] **Step 4: Render the pager in the cache inventory partial**

Replace the entire contents of `backend/app/templates/pages/_cache_inventory_table.html` with:

```jinja
{# Inventory table partial (All / Local / AI tabs). Renders the shared
   _video_list scaffold plus the shared pager; the page template sets
   innerHTML on #cache-table-region, so this partial provides table + pager. #}
{% with head_cells = "pages/_cache_head_cells.html",
        row_cells = "pages/_cache_row_cells.html",
        cache_label = "Cache",
        colspan = 7,
        empty_msg = "No entries match the current filter." %}
  {% include "pages/_video_list.html" %}
{% endwith %}
{% set _cq = 'tab=' ~ tab ~ '&limit=' ~ limit %}
{% if filters.store %}{% set _cq = _cq ~ '&store=' ~ (filters.store|urlencode) %}{% endif %}
{% if filters.workspace is not none %}{% set _cq = _cq ~ '&workspace=' ~ filters.workspace %}{% endif %}
{% if filters.orphans %}{% set _cq = _cq ~ '&orphans=1' %}{% endif %}
{% if filters.evictable %}{% set _cq = _cq ~ '&evictable=1' %}{% endif %}
{% set _range = ((offset + 1) ~ '–' ~ (offset + rows|length) ~ ' of ' ~ total) if rows else ('0 of ' ~ total) %}
{% with prev_url = ('/cache?' ~ _cq ~ '&offset=' ~ prev_offset) if prev_offset is not none else none,
        next_url = ('/cache?' ~ _cq ~ '&offset=' ~ next_offset) if next_offset is not none else none,
        range_label = _range,
        hx_target = "#cache-table-region" %}
  {% include "pages/_pager.html" %}
{% endwith %}
```

(This drops the old `{% if rows %}…{% else %}<p class="empty">` wrapper — `_video_list.html` already renders an empty-state row via `empty_msg`, matching the Clips page, and the pager now always shows like Clips.)

- [ ] **Step 5: Show the full count in the filters summary**

In `backend/app/templates/cache_page.html`, change line 92 from:

```jinja
    <summary>Filters <span class="mono muted cache-entry-count">{{ rows | length }} entries</span></summary>
```

to:

```jinja
    <summary>Filters <span class="mono muted cache-entry-count">{{ total }} entries</span></summary>
```

- [ ] **Step 6: Run the pagination tests**

Run: `.venv/bin/pytest tests/integration/test_routes_cache.py -k pagination -v`
Expected: PASS (3 passed).

- [ ] **Step 7: Run the whole cache route suite (no regression)**

Run: `.venv/bin/pytest tests/integration/test_routes_cache.py -q`
Expected: PASS (existing cache tests still green — including `test_cache_page_renders`, the tab filter tests, and the queue partial test).

- [ ] **Step 8: Commit**

```bash
git add backend/app/routes/cache.py backend/app/templates/pages/_cache_inventory_table.html backend/app/templates/cache_page.html tests/integration/test_routes_cache.py
git commit -m "feat(ui): paginate cache inventory (50/page) via shared pager"
```

---

## Task 4: Full gate + manual verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full lint/type/test gate**

```bash
.venv/bin/ruff check backend tests
.venv/bin/ruff format --check backend/app/ui/pagination.py backend/app/routes/cache.py backend/app/routes/pages/clips.py tests/unit/test_pagination.py tests/integration/test_routes_cache.py
.venv/bin/basedpyright backend tests
.venv/bin/lint-imports
.venv/bin/pytest
```

Expected: all green. (Pre-existing unrelated `ruff format` drift in `tests/integration/test_routes_pages.py` is out of scope — only check the files this plan touches, as above.)

- [ ] **Step 2: Manual browser verification**

Follow the CatDV single-seat discipline in `CLAUDE.md` before starting a server: check `lsof`/`ps` for an existing instance and reuse it; shut down with `kill -TERM` and confirm `Application shutdown complete.` in the log.

Verify in a browser:
- Cache page (`/cache`) shows ≤ 50 rows with a `‹ Prev | x–y of N | Next ›` pager.
- Next/Prev swap only the table region (URL updates via push-url; metric strip stays put).
- Switching tabs (All/Local/AI), changing Filters, and toggling orphans all reset to page 1.
- Queue tab shows no pager and keeps auto-refreshing.
- Clips list (`/`) pagination still works exactly as before.

- [ ] **Step 3: Final confirmation**

`git status` clean, `git log --oneline -4` shows the three feature commits. Report results; do not merge or open a PR unless asked.

---

## Notes for the implementer

- **No CSS changes.** The `.pager` / `.pg-btn` / `.pg-meta` styles already exist (app.css:433–451) and are reused as-is.
- **Jinja `hx_target=none`** for Clips: the default Jinja environment treats undefined/none as falsy, so `{% if hx_target %}` correctly yields plain `href` links — Clips output is unchanged.
- **interrogate:** the new `backend/app/ui/pagination.py` must keep its module + function docstrings (shown above) so docstring coverage doesn't regress. Test files are excluded.
- **CatDV seat discipline is mandatory** when running a dev server (see `CLAUDE.md`).
- **ADR:** per `CLAUDE.md`, the "shared pager partial + helper (approach A), in-memory slicing rather than API-level paging" decision is worth a short ADR under `docs/adr/` with a `docs/decisions.md` index entry once the work lands.
```
