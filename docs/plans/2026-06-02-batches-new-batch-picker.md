# New-batch Picker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Batches hub's "+ New batch" redirect with an in-page two-pane picker modal — a server-paginated clip list plus a persistent client-side "Selected" basket whose selection survives paging and filtering — that starts a run via the existing per-kind `POST /api/jobs` + `run_group` machinery.

**Architecture:** A new `GET /batches/picker` route renders clip rows through a `query_clip_page(...)` helper extracted from the clips-list route (so the picker and `GET /` share one query path) into the existing `_video_list.html` scaffold. The modal lives inside the Batches page's existing `batchesPage()` Alpine component; selection is held in a client `sel` map keyed by clip id (metadata captured from the row DOM on tick), so the basket persists across server-paginated fetches. Start reuses the bulk-annotate per-kind job creation.

**Tech Stack:** FastAPI, Jinja2 (shared `templates` env), Alpine.js + fetch + `window.htmxAlpine.reinit`, pytest + `tests/_helpers/live_ctx.install_live_ctx`.

Spec: `docs/specs/2026-06-02-batches-new-batch-picker-design.md`. Builds on branch `feat/batches-hub` (v1 Batches hub).

---

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `backend/app/routes/pages/clips.py` | Modify | Extract `query_clip_page(...)`; `clips_list` calls it (behavior-preserving) |
| `backend/app/routes/batches.py` | Modify | Add `GET /batches/picker` (live-ctx → 503 offline) |
| `backend/app/templates/pages/_batch_picker.html` | Create | Picker partial: list meta + `_video_list.html` include |
| `backend/app/templates/pages/_batch_picker_head.html` | Create | Trailing `<th>`s: Year · Type |
| `backend/app/templates/pages/_batch_picker_cells.html` | Create | Trailing `<td>`s: Year · Type |
| `backend/app/templates/pages/batches.html` | Modify | Button → `openPicker()`; two-pane modal; picker logic in `batchesPage()` |
| `backend/app/static/app.css` | Modify | `.nb-*` two-pane modal styles (tokens only) |
| `tests/integration/test_routes_batches.py` | Modify | Picker route tests |
| `docs/adr/0050-batches-new-batch-picker.md` + `docs/decisions.md` | Create/Modify | ADR (reverses 0049's redirect decision) |

---

## Task 1: Extract `query_clip_page` from the clips-list route

Behavior-preserving refactor: pull the "filters → page → cache status → `clip_summary`" body of `clips_list` into a reusable helper the picker will also call. The existing clips-list tests + N+1 pin are the guard.

**Files:**
- Modify: `backend/app/routes/pages/clips.py`
- Guard tests: `tests/integration/test_routes_pages.py`, `tests/integration/test_clips_page_perf.py`

- [ ] **Step 1: Confirm the guard tests pass before touching anything**

Run: `.venv/bin/pytest tests/integration/test_routes_pages.py tests/integration/test_clips_page_perf.py -q`
Expected: PASS (these pin the behavior the refactor must preserve).

- [ ] **Step 2: Add the `query_clip_page` helper**

In `backend/app/routes/pages/clips.py`, add this function just above the `@router.get("/")` route (after `_batch_options`):

```python
async def query_clip_page(
    ctx,
    *,
    catalog_id: str,
    q: str | None,
    offset: int,
    limit: int,
    cache_f,
    anno_f,
    batch_ids: list[int],
    host_local_proxies: bool,
) -> tuple[list[dict], int, str | None]:
    """Shared clip-page query for the clips list and the batch picker.

    Returns (clip_summary rows, total, cache_fetched_at). Encapsulates the
    host-local cache collapse, the filtered-vs-plain page fetch, the bulk
    cache-status lookup, and per-clip clip_summary. Raises ProviderError on
    archive failure (callers map to 502)."""
    # In host-local mode `cache=local` matches every clip — collapse to "any".
    effective_cache_f = "any" if (host_local_proxies and cache_f == "local") else cache_f
    cache_fetched_at: str | None = None

    if filters_active(effective_cache_f, anno_f, batch_ids):
        clips, total = await _filtered_page(
            ctx,
            catalog_id=catalog_id,
            q=q,
            offset=offset,
            limit=limit,
            cache_filter=effective_cache_f,
            anno_filter=anno_f,
            host_local_proxies=host_local_proxies,
            batch=batch_ids,
        )
    else:
        page = await ctx.archive.list_clips(
            catalog_id, ClipQuery(text=q, offset=offset, limit=limit)
        )
        clips = list(page.items)
        total = page.total
        entry = await ctx.clip_list_cache_repo.get(
            ctx.db,
            provider_id="catdv",
            catalog_id=catalog_id,
            query_text=q,
            offset=offset,
            limit=limit,
        )
        cache_fetched_at = entry["fetched_at"] if entry is not None else None

    statuses: dict[tuple[str, str], object] = {}
    if clips:
        keys = [c.key for c in clips]
        rows = await ctx.cache_inspector.status_for_clips(keys)
        statuses = {r.clip_key: r for r in rows}

    summaries = [clip_summary(c, cache_status=statuses.get(c.key)) for c in clips]
    return summaries, total, cache_fetched_at
```

- [ ] **Step 3: Rewire `clips_list` to use the helper**

In `clips_list`, replace the block that starts at `cache_fetched_at: str | None = None` and runs through the `clip_summary(...)` list comprehension inside `ctx_dict` (the `try/except ProviderError` page-fetch, the `statuses` lookup, and `"clips": [clip_summary(...) ...]`). After the refactor that region reads:

```python
    try:
        clip_rows, total, cache_fetched_at = await query_clip_page(
            ctx,
            catalog_id=catalog_id,
            q=q,
            offset=offset,
            limit=limit,
            cache_f=cache_f,
            anno_f=anno_f,
            batch_ids=batch_ids,
            host_local_proxies=host_local_proxies,
        )
    except ProviderError as exc:
        raise HTTPException(502, f"archive error: {exc}") from exc

    jobs = await ctx.jobs_repo.list_jobs(ctx.db, limit=50)
    prev_offset, next_offset = page_offsets(offset, limit, total)
    ctx_dict = {
        "q": q or "",
        "offset": offset,
        "limit": limit,
        "total": total,
        "cache_filter": cache_f,
        "anno_filter": anno_f,
        "batch_filter": batch_id,
        "batch_query": batch_query,
        "jobs": jobs,
        "batch_options": _batch_options(jobs),
        "filters_active": filters_active(effective_cache_f, anno_f, batch_ids),
        "host_local_proxies": host_local_proxies,
        "catalog": {"id": ctx.settings.catdv_catalog_id, "name": "AI katalog"},
        "clips": clip_rows,
        "prev_offset": prev_offset,
        "next_offset": next_offset,
        "cache_fetched_at": cache_fetched_at,
        "cache_age": _humanize_age(cache_fetched_at),
    }
```

Keep everything after `ctx_dict` (the `batch_status_map` loop, the per-row `draft_label`/`batch` augmentation, the template selection/return) exactly as-is — those iterate `ctx_dict["clips"]` which is now `clip_rows`. Note `effective_cache_f` is still referenced in `filters_active(...)`; keep its definition (`effective_cache_f = "any" if (host_local_proxies and cache_f == "local") else cache_f`) where it currently sits before the `try`.

- [ ] **Step 4: Run the guard tests — behavior must be unchanged**

Run: `.venv/bin/pytest tests/integration/test_routes_pages.py tests/integration/test_clips_page_perf.py -q`
Expected: PASS, including `test_clips_list_query_count_bounded`/`_identical_across_n` (the relocated queries must keep the count at 8).

- [ ] **Step 5: Commit**

```bash
git add backend/app/routes/pages/clips.py
git commit -m "refactor(clips): extract query_clip_page shared by clips list + batch picker"
```

---

## Task 2: `GET /batches/picker` route + partials

**Files:**
- Create: `backend/app/templates/pages/_batch_picker_head.html`
- Create: `backend/app/templates/pages/_batch_picker_cells.html`
- Create: `backend/app/templates/pages/_batch_picker.html`
- Modify: `backend/app/routes/batches.py`
- Test: `tests/integration/test_routes_batches.py`

- [ ] **Step 1: Write the failing route tests**

Append to `tests/integration/test_routes_batches.py` (it already has `_make_client`, `install_live_ctx`):

```python
import dataclasses
from datetime import UTC, datetime

from backend.app.archive.model import (
    CanonicalClip, ClipPage, ClipQuery, MediaRef,
)
from backend.app.archive.errors import ProviderError


def _picker_clip(clip_id=12041, name="Abramcukova_Anna_09"):
    return CanonicalClip(
        key=("catdv", str(clip_id)), name=name, duration_secs=60.0, fps=25.0,
        markers=(), fields={}, notes={},
        media=MediaRef(mime_type="video/quicktime", size_bytes=None,
                       cached_path=None, upstream_handle=str(clip_id)),
        provider_data={"ID": clip_id, "name": name}, fetched_at=datetime.now(UTC),
    )


class _PickerArchive:
    def __init__(self, clips, total=None):
        self._clips = clips
        self._total = total if total is not None else len(clips)
        self.last_query = None

    async def list_clips(self, catalog, query: ClipQuery):
        self.last_query = query
        s = query.offset
        return ClipPage(items=self._clips[s:s + query.limit], total=self._total,
                        offset=query.offset, limit=query.limit)

    async def get_clip(self, clip_id_str):
        for c in self._clips:
            if c.key[1] == clip_id_str:
                return c
        raise ProviderError("not found")


def test_batches_picker_renders_rows(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        install_live_ctx(client.app, archive=_PickerArchive([_picker_clip()]))
        r = client.get("/batches/picker")
        assert r.status_code == 200
        assert "<!doctype html>" not in r.text.lower()
        assert 'class="vlist"' in r.text
        assert 'value="catdv/12041"' in r.text          # selection checkbox
        assert "Abramcukova_Anna_09" in r.text
        assert 'id="nb-list-meta"' in r.text             # pager meta for the client
        assert 'data-total="1"' in r.text


def test_batches_picker_503_when_offline(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        # No live_ctx installed → get_live_ctx raises 503.
        r = client.get("/batches/picker")
        assert r.status_code == 503


def test_batches_picker_passes_query_and_paging(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        arch = _PickerArchive([_picker_clip(i, f"Clip_{i}") for i in range(1, 30)], total=29)
        install_live_ctx(client.app, archive=arch)
        r = client.get("/batches/picker?q=Clip&offset=12&limit=12")
        assert r.status_code == 200
        assert arch.last_query.text == "Clip"
        assert arch.last_query.offset == 12
        assert arch.last_query.limit == 12
        assert 'data-total="29"' in r.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/integration/test_routes_batches.py -k picker -q`
Expected: FAIL — 404 (route not yet defined).

- [ ] **Step 3: Create the three partials**

`backend/app/templates/pages/_batch_picker_head.html`:

```html
<th class="num col-year">Year</th>
<th class="col-type">Type</th>
```

`backend/app/templates/pages/_batch_picker_cells.html`:

```html
<td class="mono num col-year">{{ row.year or "—" }}</td>
<td class="col-type mono">{{ row.kind }}</td>
```

`backend/app/templates/pages/_batch_picker.html`:

```html
{# Batch-picker list partial. The hidden meta div carries the page total so
   the modal's client controller can render the pager; the actual rows come
   from the shared _video_list.html scaffold with picker trailing cells. #}
<div id="nb-list-meta" data-total="{{ total }}" data-offset="{{ offset }}"
     data-limit="{{ limit }}" hidden></div>
{% include "pages/_video_list.html" %}
```

- [ ] **Step 4: Add the picker route**

In `backend/app/routes/batches.py`, add the imports at the top (with the existing imports):

```python
from backend.app.archive.errors import ProviderError
from backend.app.routes.pages.clips import query_clip_page
from backend.app.services.clip_list_filters import normalize_anno, normalize_cache
```

Then add the route (after `batches_table`):

```python
@router.get("/batches/picker", response_class=HTMLResponse)
async def batches_picker(
    request: Request,
    q: str | None = None,
    cache: str | None = None,
    anno: str | None = None,
    offset: int = 0,
    limit: int = 12,
):
    """Server-paginated clip rows for the New-batch picker modal. Lists the
    CatDV catalog, so it needs live services (typed 503 offline). Selection
    is tracked client-side; this only renders one page of candidate rows."""
    ctx = get_live_ctx(request)
    catalog_id = str(ctx.settings.catdv_catalog_id)
    host_local = getattr(getattr(ctx, "proxy_resolver", None), "is_host_local", False)
    try:
        rows, total, _ = await query_clip_page(
            ctx,
            catalog_id=catalog_id,
            q=q,
            offset=offset,
            limit=limit,
            cache_f=normalize_cache(cache),
            anno_f=normalize_anno(anno),
            batch_ids=[],
            host_local_proxies=host_local,
        )
    except ProviderError as exc:
        raise HTTPException(502, f"archive error: {exc}") from exc
    return templates.TemplateResponse(
        request,
        "pages/_batch_picker.html",
        {
            "rows": rows,
            "total": total,
            "offset": offset,
            "limit": limit,
            "head_cells": "pages/_batch_picker_head.html",
            "row_cells": "pages/_batch_picker_cells.html",
            "cache_label": "Cache",
            "colspan": 5,
            "empty_msg": "No clips match.",
        },
    )
```

- [ ] **Step 5: Run tests to verify they pass + lint-imports**

Run: `.venv/bin/pytest tests/integration/test_routes_batches.py -k picker -q && .venv/bin/lint-imports`
Expected: 3 picker tests PASS; `lint-imports` reports contracts kept (batches.py importing `query_clip_page` from a pages route is allowed; no `httpx` import added).

- [ ] **Step 6: Commit**

```bash
git add backend/app/routes/batches.py backend/app/templates/pages/_batch_picker.html backend/app/templates/pages/_batch_picker_head.html backend/app/templates/pages/_batch_picker_cells.html tests/integration/test_routes_batches.py
git commit -m "feat(batches): GET /batches/picker server-paginated clip rows"
```

---

## Task 3: Two-pane picker CSS

**Files:**
- Modify: `backend/app/static/app.css`

- [ ] **Step 1: Confirm tokens exist**

Run: `grep -nE -- '--line-2:|--text-4:|--text-3:|--text-2:|--panel:|--surface:|--bg-2:|--r-2:|--accent:|--bad:|--f-mono:' backend/app/static/app.css`
Expected: all resolve. If `--line-2` or `--text-4` is absent, substitute the closest existing token in the rules below and note it.

- [ ] **Step 2: Append the picker modal styles**

Append to `backend/app/static/app.css`:

```css
/* ─── New-batch picker modal (two-pane: list + selection basket) ─────── */
.modal-card.nb-card { width: 960px; max-width: 94vw; }
.nb-body { padding: 0; display: flex; min-height: 0; max-height: calc(86vh - 112px); }
.nb-main { flex: 1; min-width: 0; display: flex; flex-direction: column; border-right: 1px solid var(--line); }
.nb-side { width: 304px; flex: none; display: flex; flex-direction: column; background: var(--panel); min-height: 0; }
.nb-filters { display: flex; align-items: center; gap: 10px; padding: 10px 14px; border-bottom: 1px solid var(--line); flex-wrap: wrap; }
.nb-filters .search { flex: 1; min-width: 150px; height: 30px; }
.nb-filters select { height: 30px; padding: 0 8px; background: var(--bg-2); color: var(--text); border: 1px solid var(--line-2); border-radius: var(--r-2); font: inherit; font-size: 12px; }
.nb-selonly { display: inline-flex; align-items: center; gap: 5px; font-size: 11.5px; color: var(--text-3); white-space: nowrap; cursor: pointer; }
.nb-list { overflow: auto; flex: 1; min-height: 220px; }
.nb-pager { display: flex; align-items: center; gap: 10px; padding: 8px 14px; border-top: 1px solid var(--line); }
.nb-pager .pg-meta { color: var(--text-3); font-size: 11.5px; font-family: var(--f-mono); }
.nb-side-h { display: flex; align-items: center; gap: 8px; padding: 11px 12px; border-bottom: 1px solid var(--line); font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-3); }
.nb-side-h b { color: var(--accent); font-size: 14px; font-family: var(--f-mono); }
.nb-basket { overflow: auto; flex: 1; min-height: 80px; padding: 8px 10px; display: flex; flex-direction: column; gap: 6px; }
.nb-basket-empty { color: var(--text-4); font-size: 12px; padding: 16px 6px; text-align: center; line-height: 1.5; }
.nb-bchip { display: flex; align-items: center; gap: 8px; padding: 5px 7px 5px 5px; border: 1px solid var(--line); border-radius: var(--r-2); background: var(--surface); }
.nb-bchip .thumb { width: 42px; height: 24px; border-radius: 3px; flex: none; object-fit: cover; background: var(--bg-2); }
.nb-bchip .nb-bname { flex: 1; min-width: 0; font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.nb-bchip .nb-bk { color: var(--text-4); font-size: 9.5px; text-transform: uppercase; letter-spacing: 0.04em; flex: none; }
.nb-bchip .nb-bx { border: 0; background: transparent; color: var(--text-4); cursor: pointer; font-size: 13px; line-height: 1; padding: 0; flex: none; }
.nb-bchip .nb-bx:hover { color: var(--bad); }
.nb-selbox { display: flex; flex-direction: column; gap: 6px; padding: 10px 12px; }
.nb-prompts { border-top: 1px solid var(--line); padding: 11px 12px; display: flex; flex-direction: column; gap: 9px; }
.nb-prompts-h { font-size: 10.5px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--text-3); }
.nb-prow { display: flex; align-items: center; gap: 8px; }
.nb-prow .tag { flex: none; }
.nb-prow select { flex: 1; min-width: 0; height: 30px; padding: 0 8px; background: var(--bg-2); color: var(--text); border: 1px solid var(--line-2); border-radius: var(--r-2); font: inherit; font-size: 12px; }
.nb-empty { padding: 26px; text-align: center; color: var(--text-3); }
.modal-actions { display: flex; gap: 8px; padding: 11px 14px; border-top: 1px solid var(--line); align-items: center; }
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/static/app.css
git commit -m "style(batches): two-pane new-batch picker modal styles"
```

---

## Task 4: Modal markup + picker logic in `batchesPage()`

Replace the redirect button with `openPicker()`, add the two-pane modal inside `.batches-page`, and extend the existing inline `batchesPage()` with picker state + methods. The whole file is given to avoid ambiguity.

**Files:**
- Modify: `backend/app/templates/pages/batches.html`
- Guard tests: `tests/integration/test_routes_batches.py`, `tests/unit/test_no_x_data_stack.py`, `tests/unit/test_htmx_alpine_single_lifecycle.py`, `tests/unit/test_templates_shared.py`

- [ ] **Step 1: Replace `batches.html` with the picker-enabled version**

Overwrite `backend/app/templates/pages/batches.html` with:

```html
{% extends "pages/layout.html" %}
{% import "components/_ui.html" as ui %}
{% block rail_active %}batches{% endblock %}
{% block title %}Batches · CatDV Annotator{% endblock %}
{% block crumb %}{{ ui.breadcrumb([('Batches', None)]) }}{% endblock %}

{% block body %}
<div class="page batches-page" x-data="batchesPage()" data-screen-label="Batches">
  <div class="page-hdr">
    <h1>Batches</h1>
    <span class="meta">annotation runs</span>
    <div class="grow"></div>
    <button type="button" class="btn primary" @click="openPicker()">+ New batch</button>
  </div>

  <div class="batches-scroll">
    <div class="metric-strip">
      <div class="metric">
        <div class="m-label">Batches</div>
        <div class="m-value">{{ metrics.total_batches }}</div>
        <div class="m-sub">{{ metrics.shown }} shown</div>
      </div>
      <div class="metric">
        <div class="m-label">Drafts produced</div>
        <div class="m-value">{{ metrics.drafts_produced }}</div>
        <div class="m-sub">across recent batches</div>
      </div>
      <div class="metric">
        <div class="m-label">Awaiting review</div>
        <div class="m-value">{{ metrics.awaiting_review }}</div>
        <div class="m-sub">{{ metrics.awaiting_batches }} batch{{ '' if metrics.awaiting_batches == 1 else 'es' }}</div>
      </div>
      <div class="metric danger">
        <div class="m-label">Failed clips</div>
        <div class="m-value">{{ metrics.failed_clips }}</div>
        <div class="m-sub">across recent batches</div>
      </div>
    </div>

    <div id="batches-table-region">
      {% include "pages/_batches_table.html" %}
    </div>
  </div>

  <!-- ───────── NEW BATCH PICKER MODAL ───────── -->
  <div class="modal" x-show="newOpen" x-cloak @keydown.escape.window="newOpen = false">
    <div class="modal-backdrop" @click="newOpen = false"></div>
    <div class="modal-card nb-card" role="dialog" aria-label="New batch">
      <div class="modal-hdr">
        <h2 class="modal-h" style="margin:0;font-size:14px">New batch</h2>
        <span class="grow"></span>
        <span class="meta mono" x-text="selCount() + ' selected'"></span>
      </div>
      <div class="nb-body">
        <div class="nb-main">
          <div class="nb-filters">
            <label class="search">
              <input type="search" x-model="nbQuery" @input.debounce.300ms="resetAndFetch()" placeholder="Search clips to add…" autocomplete="off">
            </label>
            <select x-model="nbCache" @change="resetAndFetch()" title="Cache state">
              <option value="any">Any cache</option>
              <option value="local">Local</option>
              <option value="ai">AI store</option>
              <option value="none">Not cached</option>
            </select>
            <select x-model="nbAnno" @change="resetAndFetch()" title="Annotation status">
              <option value="any">Any state</option>
              <option value="none">No annotations</option>
              <option value="for_review">For review</option>
              <option value="applied">Applied</option>
            </select>
            <label class="nb-selonly"><input type="checkbox" x-model="selOnly" @change="resetAndFetch()"> Selected only</label>
          </div>
          <div class="nb-list" id="nb-table"></div>
          <div class="nb-pager" x-show="!selOnly">
            <button type="button" class="btn sm" :disabled="nbOffset === 0" @click="goPage(-1)">‹ Prev</button>
            <span class="pg-meta" x-text="pagerLabel()"></span>
            <button type="button" class="btn sm" :disabled="nbOffset + perPage >= nbTotal" @click="goPage(1)">Next ›</button>
            <span class="grow"></span>
            <span class="pg-meta" x-text="nbTotal + ' match'"></span>
          </div>
        </div>

        <div class="nb-side">
          <div class="nb-side-h">
            <span>Selected</span><b x-text="selCount()"></b>
            <span class="grow"></span>
            <button type="button" class="btn sm ghost" x-show="selCount() > 0" @click="clearSel()">Clear</button>
          </div>
          <div class="nb-basket">
            <template x-for="c in selectedClips()" :key="c.id">
              <div class="nb-bchip">
                <img class="thumb" :src="c.thumb" alt="" loading="lazy" onerror="this.style.visibility='hidden'">
                <span class="nb-bname" x-text="c.name" :title="c.name"></span>
                <span class="nb-bk" x-text="c.kind"></span>
                <button type="button" class="nb-bx" title="Remove from selection" @click="removeSel(c.id)">✕</button>
              </div>
            </template>
            <template x-if="selCount() === 0">
              <div class="nb-basket-empty">No clips selected yet.<br>Tick clips on the left — your picks stay listed here as you page and filter.</div>
            </template>
          </div>
          <div class="nb-prompts" x-show="selectedKinds().length > 0" x-cloak>
            <div class="nb-prompts-h">Prompt per media kind</div>
            <template x-for="k in selectedKinds()" :key="k">
              <div class="nb-prow">
                <span class="tag mono" x-text="k"></span>
                <select x-model="kindPrompt[k]">
                  <option value="">— skip this kind —</option>
                  <template x-for="p in promptsForKind(k)" :key="p.id">
                    <option :value="p.current_production_version_id" x-text="p.name + ' · v' + p.current_production_version_num"></option>
                  </template>
                </select>
              </div>
            </template>
          </div>
        </div>
      </div>
      <div class="modal-actions">
        <button type="button" class="btn ghost" @click="newOpen = false">Cancel</button>
        <span class="grow"></span>
        <button type="button" class="btn primary" :disabled="!canStart()" @click="startBatch()"
                x-text="selCount() ? ('▶ Start batch — ' + runnableCount() + ' clip' + (runnableCount() === 1 ? '' : 's')) : 'Select clips'"></button>
      </div>
    </div>
  </div>
</div>

<script>
  // Batches hub controller: the read-only table (live-refreshed on the global
  // jobs SSE topic) + the New-batch picker. The picker holds selection in a
  // client-side `sel` map keyed by clip id (metadata captured from the row DOM
  // on tick), so picks survive server-paginated fetches and filtering.
  function batchesPage() {
    return {
      // ── table (v1) ──────────────────────────────────────────────
      expanded: {},
      _es: null,
      _t: null,

      init() {
        window.addEventListener("jobs-changed", () => this._schedule());
        try {
          this._es = new EventSource("/api/jobs/events");
          this._es.onmessage = () => this._schedule();
        } catch (e) { /* SSE unavailable; table is still usable */ }
        // Capture row checkbox changes inside the open picker into `sel`.
        document.addEventListener("change", (e) => {
          if (!this.newOpen) return;
          const t = e.target;
          if (t.id === "row-select-all") {
            const on = t.checked;
            document.querySelectorAll("#nb-table .row-check").forEach((cb) => {
              cb.checked = on; this._syncFromCheckbox(cb);
            });
          } else if (t.classList && t.classList.contains("row-check")) {
            this._syncFromCheckbox(t);
          }
        });
        // Auto-open pre-seeded from the clips list (sessionStorage / ?new=1).
        try {
          const seed = JSON.parse(sessionStorage.getItem("catdv:batchQueue") || "null");
          const params = new URLSearchParams(location.search);
          if (params.get("new") === "1" || (Array.isArray(seed) && seed.length)) {
            if (Array.isArray(seed)) for (const c of seed) this.sel[c.id] = { id: c.id, name: c.name || ("Clip " + c.id), kind: c.kind || "", thumb: "/api/media/" + c.id + "/thumb" };
            sessionStorage.removeItem("catdv:batchQueue");
            this.$nextTick(() => this.openPicker(true));
          }
        } catch (e) { /* no seed */ }
      },

      _schedule() { clearTimeout(this._t); this._t = setTimeout(() => this.refresh(), 500); },
      async refresh() {
        try {
          const r = await fetch("/batches/table");
          if (!r.ok) return;
          const region = document.getElementById("batches-table-region");
          if (!region) return;
          region.innerHTML = await r.text();
          window.htmxAlpine.reinit(region);
        } catch (e) { /* offline — keep current view */ }
      },
      toggle(key) { this.expanded[key] = !this.expanded[key]; },
      async retryFailed(jobIds, clipId = null) {
        const body = clipId == null ? { job_ids: jobIds } : { job_ids: jobIds, clip_ids: [clipId] };
        try {
          const r = await fetch("/batches/retry-failed", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          });
          if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(d.detail || ("HTTP " + r.status)); }
          Alpine.store("toast").push("Re-running failed clip(s)…", { level: "success" });
          this._schedule();
        } catch (e) { Alpine.store("toast").push("Retry failed: " + e.message, { level: "error" }); }
      },

      // ── new-batch picker ────────────────────────────────────────
      newOpen: false,
      nbQuery: "", nbCache: "any", nbAnno: "any", selOnly: false,
      sel: {},                 // id -> { id, name, kind, thumb }
      nbOffset: 0, perPage: 12, nbTotal: 0,
      kindPrompt: {},          // kind -> production_version_id
      _allPrompts: null,

      openPicker(keepSel = false) {
        if (!keepSel) this.sel = {};
        this.nbQuery = ""; this.nbCache = "any"; this.nbAnno = "any";
        this.selOnly = false; this.nbOffset = 0; this.kindPrompt = {};
        this.newOpen = true;
        this._loadPrompts();
        this.$nextTick(() => this.fetchPage());
      },

      async _loadPrompts() {
        if (this._allPrompts) return;
        try {
          const r = await fetch("/api/prompts?archived=0");
          if (!r.ok) return;
          this._allPrompts = (await r.json()).filter((p) => p.current_production_version_id != null);
        } catch (e) { this._allPrompts = []; }
      },
      promptsForKind(kind) {
        return (this._allPrompts || []).filter((p) => p.media_kind === kind || p.media_kind === "any");
      },

      async fetchPage() {
        const root = document.getElementById("nb-table");
        if (!root) return;
        if (this.selOnly) { this._renderSelected(root); return; }
        const params = new URLSearchParams({
          q: this.nbQuery, cache: this.nbCache, anno: this.nbAnno,
          offset: this.nbOffset, limit: this.perPage,
        });
        try {
          const r = await fetch("/batches/picker?" + params.toString());
          if (!r.ok) {
            const d = await r.json().catch(() => ({}));
            root.innerHTML = '<div class="nb-empty">' + (d.detail || "Catalog unavailable") + "</div>";
            this.nbTotal = 0;
            Alpine.store("toast").push("Catalog unavailable — connect to load clips.", { level: "error" });
            return;
          }
          root.innerHTML = await r.text();
          window.htmxAlpine.reinit(root);
          const meta = root.querySelector("#nb-list-meta");
          this.nbTotal = meta ? parseInt(meta.dataset.total || "0", 10) : 0;
          this._applyChecked(root);
        } catch (e) {
          Alpine.store("toast").push("Failed to load clips: " + e.message, { level: "error" });
        }
      },

      _syncFromCheckbox(cb) {
        const id = parseInt((cb.value.split("/")[1] || ""), 10);
        if (isNaN(id)) return;
        if (cb.checked) {
          const tr = cb.closest("tr");
          this.sel[id] = {
            id,
            name: (tr && tr.querySelector(".name") ? tr.querySelector(".name").textContent.trim() : "Clip " + id),
            kind: (tr && tr.querySelector(".col-type") ? tr.querySelector(".col-type").textContent.trim() : ""),
            thumb: (tr && tr.querySelector("img.thumb") ? tr.querySelector("img.thumb").getAttribute("src") : "/api/media/" + id + "/thumb"),
          };
        } else {
          delete this.sel[id];
          if (this.selOnly) this.$nextTick(() => this.fetchPage());
        }
      },
      _applyChecked(root) {
        const boxes = [...root.querySelectorAll(".row-check")];
        boxes.forEach((cb) => {
          const id = parseInt((cb.value.split("/")[1] || ""), 10);
          cb.checked = !!this.sel[id];
        });
        const all = root.querySelector("#row-select-all");
        if (all) all.checked = boxes.length > 0 && boxes.every((cb) => cb.checked);
      },
      _renderSelected(root) {
        const items = this.selectedClips();
        this.nbTotal = items.length;
        if (!items.length) { root.innerHTML = '<div class="nb-empty">No clips selected.</div>'; return; }
        const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
        root.innerHTML = '<div class="nb-selbox">' + items.map((c) =>
          '<label class="nb-bchip"><input type="checkbox" class="row-check" value="catdv/' + c.id + '" checked>' +
          '<img class="thumb" src="' + esc(c.thumb) + '" alt="" onerror="this.style.visibility=\'hidden\'">' +
          '<span class="nb-bname" title="' + esc(c.name) + '">' + esc(c.name) + '</span>' +
          '<span class="nb-bk col-type">' + esc(c.kind) + '</span></label>'
        ).join("") + "</div>";
      },

      resetAndFetch() { this.nbOffset = 0; this.fetchPage(); },
      goPage(d) {
        const maxOff = Math.max(0, (Math.ceil(this.nbTotal / this.perPage) - 1) * this.perPage);
        this.nbOffset = Math.max(0, Math.min(maxOff, this.nbOffset + d * this.perPage));
        this.fetchPage();
      },
      pagerLabel() {
        if (!this.nbTotal) return "No matches";
        return (this.nbOffset + 1) + "–" + Math.min(this.nbOffset + this.perPage, this.nbTotal) + " of " + this.nbTotal;
      },

      selCount() { return Object.keys(this.sel).length; },
      selectedClips() { return Object.values(this.sel); },
      selectedKinds() { return [...new Set(this.selectedClips().map((c) => c.kind).filter(Boolean))]; },
      removeSel(id) {
        delete this.sel[id];
        const cb = document.querySelector('#nb-table .row-check[value="catdv/' + id + '"]');
        if (cb) cb.checked = false;
        if (this.selOnly) this.fetchPage();
      },
      clearSel() {
        this.sel = {};
        if (this.selOnly) this.fetchPage();
        else { const root = document.getElementById("nb-table"); if (root) this._applyChecked(root); }
      },
      runnableCount() {
        const runnable = new Set(this.selectedKinds().filter((k) => this.kindPrompt[k]));
        return this.selectedClips().filter((c) => runnable.has(c.kind)).length;
      },
      canStart() { return this.runnableCount() > 0; },

      async startBatch() {
        if (!this.canStart()) return;
        const runGroup = (crypto.randomUUID && crypto.randomUUID()) || ("run-" + Date.now());
        const byKind = {};
        for (const c of this.selectedClips()) (byKind[c.kind] ||= []).push(c.id);
        const failures = [];
        for (const [kind, ids] of Object.entries(byKind)) {
          const pv = this.kindPrompt[kind];
          if (!pv) continue;
          try {
            const r = await fetch("/api/jobs", {
              method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ prompt_version_id: Number(pv), clip_ids: ids, auto_start: true, run_group: runGroup }),
            });
            if (!r.ok) failures.push(kind + ": HTTP " + r.status);
            else { const d = await r.json(); if (d.started === false) failures.push(kind + ": not started (services offline)"); }
          } catch (e) { failures.push(kind + ": " + e); }
        }
        if (failures.length) {
          Alpine.store("toast").push("Failed to start: " + failures.join(", "), { level: "error" });
          return;  // keep the modal open so the user sees the error
        }
        this.newOpen = false;
        Alpine.store("toast").push("Batch started — " + this.runnableCount() + " clip(s).", { level: "success" });
        window.dispatchEvent(new CustomEvent("jobs-changed"));
      },
    };
  }
  window.batchesPage = batchesPage;
</script>
{% endblock %}
```

- [ ] **Step 2: Verify the page route + guardrails stay green**

Run: `.venv/bin/pytest tests/integration/test_routes_batches.py tests/unit/test_no_x_data_stack.py tests/unit/test_htmx_alpine_single_lifecycle.py tests/unit/test_templates_shared.py -q`
Expected: PASS — the v1 batches page/table/retry tests still pass (the page renders; `openPicker` is wired), and the guardrails confirm no `_x_dataStack`, no stray `Alpine.initTree`/`htmx.process` (only `window.htmxAlpine.reinit` is used), single Jinja env.

- [ ] **Step 3: Confirm the redirect is gone**

Run: `grep -n "ui.button('+ New batch'" backend/app/templates/pages/batches.html || echo "redirect button removed"`
Expected: prints "redirect button removed" (the `href='/'` button is replaced by the `@click="openPicker()"` button).

- [ ] **Step 4: Commit**

```bash
git add backend/app/templates/pages/batches.html
git commit -m "feat(batches): in-page two-pane new-batch picker (no teleport, persistent selection)"
```

---

## Task 5: Full verification + ADR

**Files:**
- Create: `docs/adr/0050-batches-new-batch-picker.md`
- Modify: `docs/decisions.md`

- [ ] **Step 1: Full suite + linters**

Run:
```bash
.venv/bin/pytest -q
.venv/bin/lint-imports
.venv/bin/ruff check backend tests
```
Expected: all green except the known pre-existing failure `tests/integration/test_routes_review.py::test_clip_detail_draft_controls_show_without_review_flag` (fails on `main` too — unrelated). If anything else fails, STOP and report.

- [ ] **Step 2: Write the ADR**

Last ADR is `0049-batches-hub.md`. Create `docs/adr/0050-batches-new-batch-picker.md` in the repo's MADR-lite format (`# 0050. New-batch picker`, `**Date:** 2026-06-02`, `**Status:** Accepted`, `## Context` / `## Alternatives` / `## Decision` / `## Consequences`). Record:
- **Reverses ADR 0049's "New batch reuses the clips-list flow (redirect)" decision.** The redirect teleported the user away and, worse, relied on the clips-list DOM-checkbox selection which resets on server-side paging — so cross-page selection was impossible. Replaced by an in-page two-pane modal.
- **Selection is a client-side map keyed by clip id**, with metadata captured from the row DOM on tick, so picks survive server-paginated fetches/filters; the basket renders from the map.
- **`query_clip_page` extracted** from the clips-list route and shared with `GET /batches/picker` (one query path; the clips-list N+1 pin still holds).
- **kind/decade filters deferred** (no server-side catalog predicate); "Selected only" reuses the basket chip (no second row renderer).

- [ ] **Step 3: Update the decisions index**

Add a row for ADR 0050 to the table in `docs/decisions.md`, matching the existing column shape.

- [ ] **Step 4: Commit**

```bash
git add docs/adr/0050-batches-new-batch-picker.md docs/decisions.md
git commit -m "docs(batches): ADR 0050 — in-page new-batch picker (supersedes 0049 redirect)"
```

- [ ] **Step 5: Manual acceptance (controller/user — needs the live server + CatDV seat)**

Run through the spec's 8 manual acceptance flows on a running server (use the `server-start` / `server-stop` skills; the read-only flows 1–5 don't consume CatDV writes — flow 6's "Start batch" does). Confirm: no teleport, the basket holds picks across pages/filters, remove/clear/selected-only work, per-kind prompts + Start create a live batch, offline catalog shows the 503 message, and the clips-list flow still works.

---

## Self-Review

**Spec coverage:**
- "+ New batch" opens a modal in place (no nav) → Task 4 (`openPicker()` button + modal). ✓
- Two-pane modal: paginated list + persistent basket → Task 4 markup + `app.css` (Task 3). ✓
- Selection persists across pages/filters/search via a client map → Task 4 (`sel`, `_syncFromCheckbox`, `_applyChecked`). ✓
- Server-paginated list reusing the clips-list query → Task 1 (`query_clip_page`) + Task 2 (`/batches/picker`). ✓
- Reuse search + cache + anno filters; "Selected only" view → Task 4 (filters + `_renderSelected`). ✓
- Per-kind production-prompt assignment + Start via `POST /api/jobs` + run_group → Task 4 (`_loadPrompts`/`promptsForKind`/`startBatch`). ✓
- New batch appears + live-updates in the hub → Task 4 (`jobs-changed` dispatch → v1 `_schedule/refresh`). ✓
- Offline 503 handling → Task 2 (route) + Task 4 (`fetchPage` error branch). ✓
- kind/decade deferred; "Selected only" reuses basket chip → Task 4 + Task 5 ADR. ✓
- Clips-list "Annotate selected" unchanged → no task touches it; Task 1 is behavior-preserving (guarded by its tests). ✓

**Placeholder scan:** No TBD/TODO; every code step is complete. The ADR number (0050) is concrete (last is 0049).

**Type/name consistency:** `query_clip_page(ctx, *, catalog_id, q, offset, limit, cache_f, anno_f, batch_ids, host_local_proxies) -> (rows, total, cache_fetched_at)` is defined in Task 1 and called identically in Task 1 (clips_list) and Task 2 (picker route). The picker partial context keys (`rows`, `total`, `offset`, `limit`, `head_cells`, `row_cells`, `cache_label`, `colspan`, `empty_msg`) match `_video_list.html`'s documented params + the `#nb-list-meta` `data-total`. The client reads `data-total` (Task 4 `fetchPage`) exactly as Task 2's `_batch_picker.html` emits it. The prompt option uses `current_production_version_id` / `current_production_version_num` — the same fields `bulkAnnotate.js` / `_annotate_dropdown.html` consume from `/api/prompts`. Checkbox `value="catdv/<id>"` (from `_video_list.html`) is parsed consistently in `_syncFromCheckbox`/`_applyChecked`/`removeSel`.
