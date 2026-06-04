# Studio Archive Picker Reuse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The studio archive-picker modal renders its clip list through the existing `/batches/picker` endpoint (rich `_video_list.html` rows) instead of its own bare `picker-row` renderer, which is deleted.

**Architecture:** The studio route `/studio/_archive_picker` shrinks to rendering only the modal shell; the `archivePicker` Alpine component in `studio.js` fetches result pages from `/batches/picker` (the same endpoint the New-batch picker uses), injects the HTML, and syncs its `picked` Set against the shared `.row-check` checkboxes. One renderer, one route, for pickable clip lists.

**Tech Stack:** FastAPI + Jinja2 partials, Alpine.js 3 (no build step), pytest with `fastapi.testclient.TestClient`. No JS test runner — JS behavior is guarded by source-scan unit tests (established pattern: `tests/unit/test_no_x_data_stack.py`, `tests/unit/test_studio_setlayout_keeps_compare.py`).

**Spec:** `docs/specs/2026-06-04-studio-archive-picker-reuse-design.md`

**Branch:** `feat/studio-picker-reuse` (already created; spec is committed on it).

**Test command prefix (run from repo root):** `.venv/bin/python -m pytest`

---

## File map

| File | Action | Responsibility |
|---|---|---|
| `backend/app/templates/pages/_studio_archive_picker.html` | Rewrite | Modal shell only: header, search input, empty results target, pager, footer. No row rendering. |
| `backend/app/routes/pages/studio.py:161-192` | Slim down | `_studio_archive_picker` renders the shell; archive call deleted. |
| `backend/app/static/studio.js:141-188` | Rework | `archivePicker` gains `fetchPage`/pager/checkbox-sync; `toggle()` deleted. |
| `backend/app/static/app.css:2023-2033` | Delete | Dead `.picker-row` rules. |
| `backend/app/routes/batches.py:76-78` | Docstring | Note that `/batches/picker` is shared with the studio picker. |
| `tests/integration/test_studio_archive_picker_shell.py` | Create | Route renders shell offline; no bare rows; no HTMX search. |
| `tests/unit/test_studio_archive_picker_js.py` | Create | Source-scan: component reuses `/batches/picker` + lifecycle helper. |
| `tests/unit/test_no_picker_row.py` | Create | Repo-wide guard: `picker-row` never comes back. |

Notes for the implementer:

- The shared list rows arrive as a `<table class="vlist">` plus a hidden
  `<div id="nb-list-meta" data-total=... data-offset=... data-limit=...>`
  (see `backend/app/templates/pages/_batch_picker.html`). Checkbox values
  are `catdv/{id}`; the select-all checkbox is `#row-select-all` in the
  table head. All CSS used below already exists (`.vlist`, `.nb-card`,
  `.nb-list`, `.nb-pager`, `.nb-empty`, `.pg-meta`) — add none.
- `/batches/picker` needs live services (`get_live_ctx` → typed 503 when
  offline). The offline test client (no `CATDV_PASSWORD`) therefore can't
  exercise the rows themselves — that's covered by existing
  `/batches/picker` tests and the manual acceptance flows in the spec.

---

### Task 1: Shell-only route + template

**Files:**
- Test: `tests/integration/test_studio_archive_picker_shell.py` (create)
- Modify: `backend/app/templates/pages/_studio_archive_picker.html` (full rewrite)
- Modify: `backend/app/routes/pages/studio.py:161-192`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_studio_archive_picker_shell.py`:

```python
"""The studio archive-picker route renders only the modal shell; the result
rows come client-side from the shared /batches/picker endpoint (spec:
docs/specs/2026-06-04-studio-archive-picker-reuse-design.md). Fixture shape
mirrors tests/integration/test_studio_folders_htmx_partials.py."""

import importlib

import pytest
from fastapi.testclient import TestClient


def _setenv(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("CATDV_PASSWORD", raising=False)


@pytest.fixture
def client(monkeypatch, tmp_path):
    _setenv(monkeypatch, tmp_path)
    from backend.app import main as main_mod
    importlib.reload(main_mod)
    with TestClient(main_mod.app) as c:
        yield c


def test_picker_shell_renders_offline(client):
    r = client.get("/studio/_archive_picker?folder_id=7")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "archivePicker(7)" in r.text  # Alpine component wired
    assert "modal-results" in r.text     # empty results target
    assert "nb-pager" in r.text          # pager chrome present


def test_picker_shell_has_no_bare_rows_or_htmx_search(client):
    r = client.get("/studio/_archive_picker?folder_id=7")
    assert "picker-row" not in r.text  # bare renderer deleted
    assert "hx-get" not in r.text      # search is Alpine-driven now
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/integration/test_studio_archive_picker_shell.py -v`

Expected: `test_picker_shell_renders_offline` FAILS on `"nb-pager" in r.text`; `test_picker_shell_has_no_bare_rows_or_htmx_search` FAILS on `"picker-row" not in r.text`.

- [ ] **Step 3: Rewrite the template**

Replace the full contents of `backend/app/templates/pages/_studio_archive_picker.html` with:

```html
{# Archive picker modal — search + multi-select add into a folder.

   The modal is HTMX-swapped into #modal-root from the "+ Add from archive"
   button inside an expanded folder (see _studio_folder.html). The modal
   manages its own state via the `archivePicker(folderId)` Alpine
   component; the close action wipes #modal-root.

   The result rows are NOT rendered here: the component fetches pages from
   the shared /batches/picker endpoint (the same rich _video_list.html rows
   the New-batch picker uses) and injects them into .modal-results.
#}
<div class="modal" x-data="archivePicker({{ folder_id }})" @keydown.escape.window="close()">
  <div class="modal-backdrop" @click="close()"></div>
  <div class="modal-card nb-card">
    <div class="modal-hdr">
      <h2>Add clips to folder</h2>
      <span class="grow"></span>
      <button class="btn ghost" @click="close()">×</button>
    </div>
    <div class="modal-body">
      <input type="search" placeholder="search clips…"
             x-model="q" @input.debounce.300ms="resetAndFetch()">
      <div class="modal-results nb-list" @change="onCheckChange($event)"></div>
      <div class="nb-pager">
        <button type="button" class="btn sm" :disabled="offset === 0" @click="goPage(-1)">‹ Prev</button>
        <span class="pg-meta" x-text="pagerLabel()"></span>
        <button type="button" class="btn sm" :disabled="offset + limit >= total" @click="goPage(1)">Next ›</button>
        <span class="grow"></span>
        <span class="pg-meta" x-text="total + ' match'"></span>
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

(`nb-card` widens the modal to fit the table; `nb-list` gives the results region scroll + min-height. Both exist in `app.css`.)

- [ ] **Step 4: Slim the route**

In `backend/app/routes/pages/studio.py`, replace the whole `_studio_archive_picker` handler (currently lines 161–192, from `@router.get("/studio/_archive_picker"...)` through the closing `)` of its `TemplateResponse`) with:

```python
@router.get("/studio/_archive_picker", response_class=HTMLResponse)
async def _studio_archive_picker(request: Request, folder_id: int):
    """Renders the archive-picker modal shell only. The result rows are
    fetched client-side from the shared /batches/picker endpoint (the same
    rich _video_list.html rows the New-batch picker renders), so this route
    has no archive dependency and works offline."""
    return templates.TemplateResponse(
        request,
        "pages/_studio_archive_picker.html",
        {"folder_id": folder_id},
    )
```

The deleted body removed this route's only uses of `ClipQuery` (a function-local import) and of `q` — nothing else to clean up; `_archive` and `get_core_ctx` are still used by other handlers in the file, so their imports stay.

- [ ] **Step 5: Run the new test, then the studio + batches test files**

Run: `.venv/bin/python -m pytest tests/integration/test_studio_archive_picker_shell.py -v`
Expected: 2 passed.

Run: `.venv/bin/python -m pytest tests/integration -k "studio" -q && .venv/bin/python -m pytest tests/integration -k "batches" -q`
Expected: all pass (no studio test referenced the old results rendering — verified during planning).

- [ ] **Step 6: Commit**

```bash
git add tests/integration/test_studio_archive_picker_shell.py \
        backend/app/templates/pages/_studio_archive_picker.html \
        backend/app/routes/pages/studio.py
git commit -m "refactor(studio): archive picker route renders shell only"
```

---

### Task 2: `archivePicker` fetches the shared picker rows

**Files:**
- Test: `tests/unit/test_studio_archive_picker_js.py` (create)
- Modify: `backend/app/static/studio.js:141-188` (the `archivePicker` component)
- Modify: `docs/specs/2026-06-04-studio-archive-picker-reuse-design.md` (one-line amendment)

- [ ] **Step 1: Write the failing source-scan test**

Create `tests/unit/test_studio_archive_picker_js.py`:

```python
"""Guard: the studio archive picker fetches its rows from the shared
/batches/picker endpoint (one renderer for pickable clip lists) instead of
rendering its own.

Source-scan guard — the repo has no JS test runner; brace-matching shape
mirrors tests/unit/test_studio_setlayout_keeps_compare.py.
"""

from pathlib import Path

STUDIO_JS = Path("backend/app/static/studio.js")


def _component_body(text: str, marker: str) -> str:
    """Source of the component from `marker` to its balanced closing brace."""
    start = text.index(marker)
    brace = text.index("{", start)
    depth = 0
    for i in range(brace, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise AssertionError(f"{marker} body not terminated — no closing brace")


def test_archive_picker_reuses_batches_picker_endpoint():
    body = _component_body(
        STUDIO_JS.read_text(encoding="utf-8"), "Alpine.data('archivePicker'"
    )
    assert "/batches/picker" in body, (
        "archivePicker must fetch rows from the shared /batches/picker "
        "endpoint, not render its own list"
    )
    assert "htmxAlpine.reinit" in body, (
        "fetch-injected rows must go through the shared lifecycle helper"
    )
    assert "nb-list-meta" in body, (
        "pager total must come from the shared #nb-list-meta div"
    )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_studio_archive_picker_js.py -v`
Expected: FAIL — `"/batches/picker" in body` is False.

- [ ] **Step 3: Rework the component**

In `backend/app/static/studio.js`, replace the whole `Alpine.data('archivePicker', ...)` registration (currently lines 141–188, from `Alpine.data('archivePicker', (folderId) => ({` through its closing `}));`) with:

```js
  Alpine.data('archivePicker', (folderId) => ({
    folderId,
    picked: new Set(),
    q: '',
    offset: 0,
    limit: 15,
    total: 0,

    init() { this.fetchPage(); },

    // ── results page (shared /batches/picker renderer) ─────────────
    async fetchPage() {
      const root = this.$root.querySelector('.modal-results');
      if (!root) return;
      const params = new URLSearchParams({
        q: this.q, offset: this.offset, limit: this.limit,
      });
      try {
        const r = await fetch('/batches/picker?' + params.toString());
        if (!r.ok) {
          const d = await r.json().catch(() => ({}));
          root.innerHTML = '<div class="nb-empty">' + (d.detail || 'Catalog unavailable') + '</div>';
          this.total = 0;
          Alpine.store('toast').push('Catalog unavailable — connect to load clips.', { level: 'error' });
          return;
        }
        root.innerHTML = await r.text();
        window.htmxAlpine.reinit(root);
        const meta = root.querySelector('#nb-list-meta');
        this.total = meta ? parseInt(meta.dataset.total || '0', 10) : 0;
        this._applyChecked(root);
      } catch (e) {
        Alpine.store('toast').push('Failed to load clips: ' + e.message, { level: 'error' });
      }
    },

    resetAndFetch() { this.offset = 0; this.fetchPage(); },
    goPage(d) {
      const maxOff = Math.max(0, (Math.ceil(this.total / this.limit) - 1) * this.limit);
      this.offset = Math.max(0, Math.min(maxOff, this.offset + d * this.limit));
      this.fetchPage();
    },
    pagerLabel() {
      if (!this.total) return 'No matches';
      return (this.offset + 1) + '–' + Math.min(this.offset + this.limit, this.total) + ' of ' + this.total;
    },

    // ── selection sync (checkboxes come from the shared rows) ──────
    onCheckChange(e) {
      const t = e.target;
      if (t.id === 'row-select-all') {
        this.$root.querySelectorAll('.modal-results .row-check').forEach((cb) => {
          cb.checked = t.checked;
          this._syncOne(cb);
        });
      } else if (t.classList && t.classList.contains('row-check')) {
        this._syncOne(t);
      }
    },
    _syncOne(cb) {
      const id = parseInt(cb.value.split('/')[1] || '', 10);
      if (isNaN(id)) return;
      if (cb.checked) this.picked.add(id);
      else this.picked.delete(id);
    },
    _applyChecked(root) {
      const boxes = [...root.querySelectorAll('.row-check')];
      boxes.forEach((cb) => {
        const id = parseInt(cb.value.split('/')[1] || '', 10);
        cb.checked = this.picked.has(id);
      });
      const all = root.querySelector('#row-select-all');
      if (all) all.checked = boxes.length > 0 && boxes.every((cb) => cb.checked);
    },

    async addSelected() {
      const ids = Array.from(this.picked);
      if (!ids.length) return;
      const res = await fetch(`/api/studio/folders/${this.folderId}/clips`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'HX-Request': 'true'},
        body: JSON.stringify({clip_ids: ids}),
      });
      if (res.ok) {
        const html = await res.text();
        const kidsEl = document.querySelector(
          `.studio-folder[data-folder-id="${this.folderId}"] .studio-folder-kids`
        );
        if (kidsEl) {
          kidsEl.innerHTML = html;
          window.htmxAlpine.reinit(kidsEl);
        } else {
          console.warn(
            `archivePicker.addSelected: .studio-folder-kids not found for folder ${this.folderId}`
          );
        }
        this.close();  // close the archive picker modal
        Alpine.store('toast').push(
          `Added ${ids.length} clip${ids.length === 1 ? '' : 's'} to folder.`,
          { level: 'success' },
        );
      } else {
        Alpine.store('toast').push(
          `Add clips failed (HTTP ${res.status}).`,
          { level: 'error' },
        );
      }
    },

    close() {
      const root = document.getElementById('modal-root');
      if (root) root.innerHTML = '';
    },
  }));
```

(`addSelected` and `close` are byte-identical to the current code. The old
`toggle(id)` is **deleted**: the shared rows carry no per-row Alpine
binding, so `onCheckChange` on the results container replaces it. Checkbox
`change` events bubble, including from `#row-select-all` in the injected
table head. Note: checkbox values are `catdv/{id}` — the `split('/')` parse
matches `batchesPage._syncFromCheckbox` in `pages/batches.html`.)

- [ ] **Step 4: Amend the spec to match**

In `docs/specs/2026-06-04-studio-archive-picker-reuse-design.md`, replace the line:

```
Unchanged: `toggle(id)`, `addSelected()`, `close()`.
```

with:

```
Unchanged: `addSelected()`, `close()`. `toggle(id)` is deleted — the
shared rows have no per-row Alpine binding; the container-level
`@change` handler replaces it.
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/unit/test_studio_archive_picker_js.py tests/unit/test_htmx_alpine_single_lifecycle.py tests/unit/test_no_x_data_stack.py -v`
Expected: all pass. (The lifecycle test stays green because the new code calls `window.htmxAlpine.reinit`, not `Alpine.initTree`/`htmx.process`.)

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_studio_archive_picker_js.py \
        backend/app/static/studio.js \
        docs/specs/2026-06-04-studio-archive-picker-reuse-design.md
git commit -m "refactor(studio): archive picker reuses /batches/picker rows"
```

---

### Task 3: Repo-wide `picker-row` guard + dead CSS deletion

**Files:**
- Test: `tests/unit/test_no_picker_row.py` (create)
- Modify: `backend/app/static/app.css:2023-2033`

- [ ] **Step 1: Write the failing guard test**

Create `tests/unit/test_no_picker_row.py`:

```python
"""Guard: the bare `picker-row` clip-list renderer is gone — pickable clip
lists render through the shared _video_list.html scaffold served by
/batches/picker (spec:
docs/specs/2026-06-04-studio-archive-picker-reuse-design.md).

Scan shape mirrors tests/unit/test_no_x_data_stack.py: every file under
static/ and templates/, vendor excluded.
"""

from pathlib import Path

STATIC = Path("backend/app/static")
TEMPLATES = Path("backend/app/templates")
NEEDLE = "picker-row"


def _scan(root: Path) -> list[str]:
    hits: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "vendor" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if NEEDLE in text:
            hits.append(str(path))
    return hits


def test_no_picker_row_renderer():
    hits = _scan(STATIC) + _scan(TEMPLATES)
    assert hits == [], (
        f"bare '{NEEDLE}' renderer found — render through the shared "
        f"/batches/picker rows (_video_list.html) instead: {hits}"
    )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_no_picker_row.py -v`
Expected: FAIL with `hits == ['backend/app/static/app.css']` (the template was already cleaned in Task 1).

- [ ] **Step 3: Delete the dead CSS**

In `backend/app/static/app.css`, delete these rules (lines 2023–2033) and the blank line that follows them:

```css
.picker-row {
  display: flex; gap: 8px; align-items: center;
  padding: 4px 6px;
  border-radius: 4px;
}
.picker-row:hover {
  background: var(--surface-2);
}
.picker-row .name {
  flex: 1;
}
```

Keep the neighbouring `.modal-results` and `.modal-body input[type=search]` rules — the new shell still uses both.

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_no_picker_row.py tests/unit/test_studio_css_no_phantom_tokens.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_no_picker_row.py backend/app/static/app.css
git commit -m "refactor(css): drop dead .picker-row rules; guard against return"
```

---

### Task 4: Shared-endpoint docstring + full suite

**Files:**
- Modify: `backend/app/routes/batches.py:76-78` (docstring only)

- [ ] **Step 1: Update the docstring**

In `backend/app/routes/batches.py`, replace the `batches_picker` docstring:

```python
    """Server-paginated clip rows for the New-batch picker modal. Lists the
    CatDV catalog, so it needs live services (typed 503 offline). Selection
    is tracked client-side; this only renders one page of candidate rows."""
```

with:

```python
    """Server-paginated clip rows for the New-batch picker modal AND the
    Studio archive-picker modal (the shared pickable-clip-list renderer —
    see docs/specs/2026-06-04-studio-archive-picker-reuse-design.md). Lists
    the CatDV catalog, so it needs live services (typed 503 offline).
    Selection is tracked client-side; this renders one page of rows."""
```

- [ ] **Step 2: Run the full test suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass, no new failures vs. `main`. If anything unrelated is already red on `main`, note it but don't fix it here.

- [ ] **Step 3: Run the import-linter contracts**

Run: `.venv/bin/lint-imports`
Expected: all contracts kept (this change adds no imports).

- [ ] **Step 4: Commit**

```bash
git add backend/app/routes/batches.py
git commit -m "docs(batches): note /batches/picker is shared with studio picker"
```

---

### Task 5: Manual acceptance + finish

- [ ] **Step 1: Walk the spec's manual acceptance flows**

The five flows live at the bottom of
`docs/specs/2026-06-04-studio-archive-picker-reuse-design.md`. Flows 1–3
and 5 need live CatDV; flow 4 is the offline check. Use the
`server-start` skill to boot the app (seat discipline!), walk the flows,
`server-stop` when done. Defer this step if no CatDV seat is available —
the user decides when (per their verification-sequencing preference,
read-only manual checks are fine anytime; these flows only write studio
folder rows locally, except flow 1's "Add" which writes to the local DB
only — no CatDV writes anywhere).

- [ ] **Step 2: Decide integration**

Use the superpowers:finishing-a-development-branch skill — the expected
outcome per the user's workflow is a PR from `feat/studio-picker-reuse`
into `main` (check for divergence and rebase before pushing).

---

## Self-review notes (done at planning time)

- **Spec coverage:** shell template+route → Task 1; JS rework → Task 2;
  CSS + guard → Task 3; docstring → Task 4; manual flows → Task 5. The
  spec's "Testing strategy" items 1/2/3 map to Tasks 1/3/4 respectively. ✓
- **Type consistency:** component state names (`q`, `offset`, `limit`,
  `total`, `picked`) match between the template bindings (Task 1) and the
  JS (Task 2); checkbox value format `catdv/{id}` matches
  `_video_list.html` (`row.select_value`) and the existing batches parse. ✓
- **No placeholders:** every code step carries the full code. ✓
