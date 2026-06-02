# Draft Review Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework the clip-detail **Draft** scope into the new design — readable Proposed/Accepted proposal cards with Accept / Edit / Delete, a review bar (Accept all · count · ‹ Clip i/N › · Apply), and a 3-color timeline (proposed/accepted/editing) — reusing the existing scope toggle, player marker drag-edit, review queue, and decision/apply API.

**Architecture:** The Draft panel becomes Alpine-data-driven: the page serializes draft items to `draftMarkers`/`draftFields`/`draftNotes` arrays (each with `item_id` + `status`, rejected excluded) via a new `draft_review_arrays` view-model. A `reviewMixin` (in `review.js`), merged into the clip `x-data`, manages status/edits/counts and persists through the existing `POST /api/review/items/{id}/decision` + `/clips/{id}/apply`; marker timeline edits reuse `player.js::_persistMarker`. The batch **Review →** seeds the existing `reviewQueue` from a new `GET /batches/review-queue`. A JSON `draft-data` endpoint refreshes the arrays after apply/annotate.

**Tech Stack:** FastAPI, Jinja2, Alpine.js + fetch + `window.htmxAlpine.reinit`, pytest.

Spec: `docs/specs/2026-06-02-draft-review-redesign-design.md`. Branch `feat/batches-hub`.

---

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `backend/app/ui/view_models.py` | Modify | `draft_review_arrays(draft)` — markers/fields/notes arrays w/ `status`, rejected excluded |
| `tests/unit/test_draft_review_arrays.py` | Create | Unit tests for the serializer |
| `backend/app/routes/review.py` | Modify | `GET /api/review/clips/{id}/draft-data` (JSON refresh) |
| `backend/app/repositories/review_items.py` | Modify | `pending_clip_ids_for_jobs(job_ids)` |
| `backend/app/routes/batches.py` | Modify | `GET /batches/review-queue?job_ids=…` |
| `tests/integration/test_review_queue_and_draft_data.py` | Create | Tests for the two new endpoints + repo method |
| `backend/app/static/review.js` | Modify | Replace `reviewQueue` DOM-reading with the data-driven `reviewMixin` |
| `backend/app/templates/pages/_anno_draft.html` | Modify | Alpine card panel (markers/fields/notes tabs) + review bar |
| `backend/app/templates/pages/clip_detail.html` | Modify | x-data: draft arrays + reviewMixin + scope; markers-row `x_show`; remove old review-actionbar |
| `backend/app/templates/pages/_player_overlay.html` | Modify | draft range `is-accepted` class |
| `backend/app/templates/pages/_batches_table.html` | Modify | Review → seeds the queue (JS) |
| `backend/app/static/app.css` | Modify | `.ri-*` cards, `.review-bar`, `.edit-hint`, 3-color `.draft-range` |
| `docs/adr/0051-draft-review-redesign.md` + `docs/decisions.md` | Create/Modify | ADR |

---

## Task 1: `draft_review_arrays` serializer

**Files:**
- Modify: `backend/app/ui/view_models.py`
- Test: `tests/unit/test_draft_review_arrays.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_draft_review_arrays.py`:

```python
from backend.app.ui.view_models import draft_review_arrays


def _draft():
    return {
        "has_draft": True,
        "markers": [
            {"item_id": 1, "decision": "pending", "name": "A", "category": "est",
             "description": "d", "in_secs": 2.0, "out_secs": 5.0, "color": None, "kind": "marker"},
            {"item_id": 2, "decision": "accepted", "name": "B", "category": None,
             "description": None, "in_secs": 8.0, "out_secs": None, "color": None, "kind": "marker"},
            {"item_id": 3, "decision": "rejected", "name": "C", "category": None,
             "description": None, "in_secs": 9.0, "out_secs": None, "color": None, "kind": "marker"},
        ],
        "fields": [
            {"item_id": 11, "decision": "pending", "identifier": "x.y", "value": "v", "multi": False, "kind": "field"},
        ],
        "note_items": [
            {"item_id": 21, "decision": "accepted", "identifier": None, "text": "note", "kind": "note"},
            {"item_id": 22, "decision": "rejected", "identifier": None, "text": "gone", "kind": "note"},
        ],
    }


def test_markers_carry_status_and_exclude_rejected():
    a = draft_review_arrays(_draft())
    assert [m["item_id"] for m in a["markers"]] == [1, 2]          # 3 (rejected) dropped
    assert a["markers"][0]["status"] == "proposed"                # pending -> proposed
    assert a["markers"][1]["status"] == "accepted"
    assert a["markers"][0]["in_secs"] == 2.0 and a["markers"][0]["out_secs"] == 5.0
    assert a["markers"][0]["name"] == "A" and a["markers"][0]["category"] == "est"


def test_fields_and_notes_status_and_exclude_rejected():
    a = draft_review_arrays(_draft())
    assert [f["item_id"] for f in a["fields"]] == [11]
    assert a["fields"][0]["status"] == "proposed"
    assert a["fields"][0]["identifier"] == "x.y" and a["fields"][0]["value"] == "v"
    assert [n["item_id"] for n in a["notes"]] == [21]             # 22 (rejected) dropped
    assert a["notes"][0]["status"] == "accepted" and a["notes"][0]["text"] == "note"


def test_no_draft_returns_empty_arrays():
    assert draft_review_arrays({"has_draft": False}) == {"markers": [], "fields": [], "notes": []}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_draft_review_arrays.py -q`
Expected: FAIL — `ImportError: cannot import name 'draft_review_arrays'`.

- [ ] **Step 3: Implement the serializer**

Append to `backend/app/ui/view_models.py`:

```python
def draft_review_arrays(draft: dict) -> dict:
    """Shape a `build_draft_view` result into the Alpine arrays the redesigned
    Draft panel + 3-color timeline render from. Each item carries `item_id` and
    a `status` ("accepted" if its review_item decision is accepted, else
    "proposed"). Rejected items are excluded entirely (Delete = reject hides
    them). Pure function — unit-tested in isolation."""
    if not draft.get("has_draft"):
        return {"markers": [], "fields": [], "notes": []}

    def _status(decision) -> str:
        return "accepted" if decision == "accepted" else "proposed"

    markers = [
        {
            "item_id": m["item_id"],
            "status": _status(m.get("decision")),
            "name": m.get("name") or "",
            "category": m.get("category"),
            "description": m.get("description"),
            "in_secs": m["in_secs"],
            "out_secs": m.get("out_secs"),
            "color": m.get("color"),
        }
        for m in draft.get("markers", [])
        if m.get("decision") != "rejected"
    ]
    fields = [
        {
            "item_id": f["item_id"],
            "status": _status(f.get("decision")),
            "identifier": f.get("identifier") or "",
            "value": f.get("value", ""),
            "multi": bool(f.get("multi")),
        }
        for f in draft.get("fields", [])
        if f.get("decision") != "rejected"
    ]
    notes = [
        {
            "item_id": n["item_id"],
            "status": _status(n.get("decision")),
            "text": n.get("text") or "",
        }
        for n in draft.get("note_items", [])
        if n.get("decision") != "rejected"
    ]
    return {"markers": markers, "fields": fields, "notes": notes}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_draft_review_arrays.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/ui/view_models.py tests/unit/test_draft_review_arrays.py
git commit -m "feat(review): draft_review_arrays view-model (status + exclude rejected)"
```

---

## Task 2: `pending_clip_ids_for_jobs` + `/batches/review-queue`

**Files:**
- Modify: `backend/app/repositories/review_items.py`
- Modify: `backend/app/routes/batches.py`
- Test: `tests/integration/test_review_queue_and_draft_data.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_review_queue_and_draft_data.py`:

```python
import asyncio
import importlib

from fastapi.testclient import TestClient

from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.repositories.review_items import ReviewItemsRepo


def _make_client(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "7")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    return TestClient(main_mod.app)


async def _seed(ctx):
    prompts = PromptsRepo()
    _, vid = await prompts.create_with_initial_version(
        ctx.db, name="P", description=None, body="b",
        target_map={"x": {"kind": "markers"}}, output_schema={}, model="m",
    )
    jobs = JobsRepo()
    jid = await jobs.create_job(ctx.db, prompt_version_id=vid, clip_ids=[101, 102], run_group="rg-1")
    # annotation + pending review item for clip 101 (102 has none -> not pending)
    cur = await ctx.db.execute(
        "INSERT INTO annotations (catdv_clip_id, catdv_clip_name, prompt_version_id, job_id, "
        " model, prompt_used, raw_response, structured_output, clip_snapshot, created_at) "
        "VALUES (101, 'C101', ?, ?, 'm', 'p', '{}', '{}', '{}', '2026-06-02T00:00:00')",
        (vid, jid),
    )
    ann = cur.lastrowid
    await ctx.db.execute(
        "INSERT INTO review_items (annotation_id, studio_run_id, catdv_clip_id, kind, "
        " target_identifier, proposed_value, edited_value, decision, applied_at) "
        "VALUES (?, NULL, 101, 'marker', NULL, '{}', NULL, 'pending', NULL)",
        (ann,),
    )
    await ctx.db.commit()
    return jid


def test_pending_clip_ids_for_jobs(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        ctx = client.app.state.core_ctx
        jid = asyncio.run(_seed(ctx))
        ids = asyncio.run(ReviewItemsRepo().pending_clip_ids_for_jobs(ctx.db, [jid]))
        assert ids == [101]
        assert asyncio.run(ReviewItemsRepo().pending_clip_ids_for_jobs(ctx.db, [])) == []


def test_review_queue_route(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        jid = asyncio.run(_seed(client.app.state.core_ctx))
        r = client.get(f"/batches/review-queue?job_ids={jid}")
        assert r.status_code == 200
        assert r.json() == {"clip_ids": [101]}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_review_queue_and_draft_data.py -q`
Expected: FAIL — `AttributeError: 'ReviewItemsRepo' object has no attribute 'pending_clip_ids_for_jobs'`.

- [ ] **Step 3: Add the repo method**

In `backend/app/repositories/review_items.py`, add to `ReviewItemsRepo`:

```python
    async def pending_clip_ids_for_jobs(
        self, conn: aiosqlite.Connection, job_ids: list[int]
    ) -> list[int]:
        """Ordered distinct clip ids with un-applied review items across the
        given jobs (a batch's member jobs), newest annotation first — the
        review-walk queue for a batch. `job_ids` is bounded by the batch, so a
        single IN clause is safe."""
        if not job_ids:
            return []
        placeholders = ",".join("?" * len(job_ids))
        cur = await conn.execute(
            f"""
            SELECT ri.catdv_clip_id AS clip_id, MAX(a.created_at) AS created_at
            FROM review_items ri
            JOIN annotations a ON a.id = ri.annotation_id
            WHERE ri.applied_at IS NULL AND a.job_id IN ({placeholders})
            GROUP BY ri.catdv_clip_id
            ORDER BY created_at DESC, ri.catdv_clip_id DESC
            """,
            tuple(job_ids),
        )
        return [int(r[0]) for r in await cur.fetchall()]
```

- [ ] **Step 4: Add the route**

In `backend/app/routes/batches.py`, add (after `batches_table`):

```python
@router.get("/batches/review-queue")
async def batches_review_queue(request: Request, job_ids: str = ""):
    """Ordered pending clip ids for a batch's jobs — seeds the clip-detail
    review walk. Pure DB (offline-safe)."""
    ctx = get_core_ctx(request)
    ids = [int(x) for x in job_ids.split(",") if x.strip().isdigit()]
    clip_ids = await ctx.review_items_repo.pending_clip_ids_for_jobs(ctx.db, ids)
    return {"clip_ids": clip_ids}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/integration/test_review_queue_and_draft_data.py -q && .venv/bin/lint-imports`
Expected: 2 tests PASS; contracts kept.

- [ ] **Step 6: Commit**

```bash
git add backend/app/repositories/review_items.py backend/app/routes/batches.py tests/integration/test_review_queue_and_draft_data.py
git commit -m "feat(review): pending_clip_ids_for_jobs + GET /batches/review-queue"
```

---

## Task 3: `GET /api/review/clips/{id}/draft-data`

**Files:**
- Modify: `backend/app/routes/review.py`
- Test: `tests/integration/test_review_queue_and_draft_data.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_review_queue_and_draft_data.py`:

```python
def test_draft_data_route(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        asyncio.run(_seed(client.app.state.core_ctx))
        r = client.get("/api/review/clips/101/draft-data")
        assert r.status_code == 200
        body = r.json()
        assert set(body.keys()) == {"markers", "fields", "notes"}
        # the seeded pending marker item is present as a "proposed" card
        assert any(m["status"] == "proposed" for m in body["markers"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_review_queue_and_draft_data.py -k draft_data -q`
Expected: FAIL — 404.

- [ ] **Step 3: Add the route**

In `backend/app/routes/review.py`, add the import at the top (with the existing imports):

```python
from backend.app.ui.view_models import draft_review_arrays
```

Then add the route (after `list_items_for_clip`):

```python
@router.get("/clips/{clip_id}/draft-data")
async def draft_data(request: Request, clip_id: int):
    """JSON draft arrays (markers/fields/notes with item_id + status, rejected
    excluded) for the redesigned Draft panel to (re)hydrate its Alpine state —
    e.g. after Apply or Annotate — without swapping server HTML into the
    reactive subtree."""
    ctx = get_core_ctx(request)
    draft = await _build_draft_for_clip(ctx, clip_id)
    return draft_review_arrays(draft)
```

(`_build_draft_for_clip` is already imported in `review.py`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/integration/test_review_queue_and_draft_data.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/routes/review.py tests/integration/test_review_queue_and_draft_data.py
git commit -m "feat(review): GET /api/review/clips/{id}/draft-data JSON refresh"
```

---

## Task 4: `reviewMixin` (data-driven review component)

Replace the DOM-checkbox `reviewQueue` with a mixin operating on the draft
arrays. Keep the queue/walk (sessionStorage + `?review=1`) and the persistence
endpoints.

**Files:**
- Modify: `backend/app/static/review.js` (full rewrite of the exported component)

- [ ] **Step 1: Rewrite `review.js`**

Replace the entire contents of `backend/app/static/review.js` with:

```javascript
// reviewMixin — data-driven Draft review, composed into the clip-detail
// x-data alongside player() + clipAnnotate(). Operates on the Alpine arrays
// draftMarkers / draftFields / draftNotes (each item: { item_id, status, … }).
// Persists via /api/review/items/{id}/decision and /clips/{id}/apply; marker
// in/out edits go through player.js::_persistMarker. Walks a clip queue held
// in sessionStorage['catdv:reviewQueue'] (seeded by the clips list / batches).
function reviewMixin(clipId) {
  return {
    reviewQueue: [],
    _reviewInit() {
      try { this.reviewQueue = JSON.parse(sessionStorage.getItem("catdv:reviewQueue") || "[]"); }
      catch (e) { this.reviewQueue = []; }
    },
    // ── counts ────────────────────────────────────────────────────
    _allDraft() { return [...this.draftMarkers, ...this.draftFields, ...this.draftNotes]; },
    totalCount() { return this._allDraft().length; },
    acceptedCount() { return this._allDraft().filter(it => it.status === "accepted").length; },
    // ── queue / walk ─────────────────────────────────────────────
    _qIdx() { return this.reviewQueue.indexOf(clipId); },
    reviewPos() { const i = this._qIdx(); return i >= 0 ? (i + 1) : 1; },
    reviewLen() { const i = this._qIdx(); return i >= 0 ? this.reviewQueue.length : 1; },
    navClip(d) {
      const i = this._qIdx();
      if (i < 0) return;
      const t = i + d;
      if (t < 0 || t >= this.reviewQueue.length) return;
      location.href = `/clips/${this.reviewQueue[t]}?review=1&scope=draft`;
    },
    // ── accept / delete / edit ───────────────────────────────────
    async _persist(item, decision, editedValue) {
      const body = { decision };
      if (editedValue !== undefined) body.edited_value = editedValue;
      try {
        const r = await fetch(`/api/review/items/${item.item_id}/decision`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
      } catch (e) {
        Alpine.store("toast").push(`Decision not saved: ${e.message || e}`, { level: "error" });
      }
    },
    toggleAccept(item) {
      item.status = item.status === "accepted" ? "proposed" : "accepted";
      this._persist(item, item.status === "accepted" ? "accepted" : "pending");
    },
    acceptAll() {
      for (const it of this._allDraft()) {
        if (it.status !== "accepted") { it.status = "accepted"; this._persist(it, "accepted"); }
      }
    },
    del(item, ev) {
      if (ev) ev.stopPropagation();
      for (const key of ["draftMarkers", "draftFields", "draftNotes"]) {
        const i = this[key].findIndex(x => x.item_id === item.item_id);
        if (i >= 0) { this[key].splice(i, 1); break; }
      }
      if (this.editingItemId === item.item_id) this.editingItemId = null;
      this._persist(item, "rejected");
      Alpine.store("toast").push("Proposal deleted.", { level: "info" });
    },
    toggleEdit(itemId) {
      this.editingItemId = (this.editingItemId === itemId ? null : itemId);
      const m = this.draftMarkers.find(x => x.item_id === itemId);
      if (this.editingItemId && m) this.seek(m.in_secs);
    },
    // Persist a field/note edit (markers persist via player._persistMarker).
    persistField(item) { item.status = "accepted"; this._persist(item, "accepted", item.value); },
    persistNote(item) { item.status = "accepted"; this._persist(item, "accepted", item.text); },
    // ── apply (stay) + refresh ───────────────────────────────────
    async applyDraft() {
      try {
        const r = await fetch(`/api/review/clips/${clipId}/apply`, { method: "POST" });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        Alpine.store("toast").push("Changes applied.", { level: "success" });
        await this.refreshDraft();
      } catch (e) {
        Alpine.store("toast").push(`Apply failed: ${e.message || e}. Nothing was applied.`, { level: "error" });
      }
    },
    async refreshDraft() {
      try {
        const r = await fetch(`/api/review/clips/${clipId}/draft-data`);
        if (!r.ok) return;
        const d = await r.json();
        // Replace arrays in place so player()'s draftMarkers ref stays bound.
        this.draftMarkers.splice(0, this.draftMarkers.length, ...d.markers);
        this.draftFields.splice(0, this.draftFields.length, ...d.fields);
        this.draftNotes.splice(0, this.draftNotes.length, ...d.notes);
        this.editingItemId = null;
      } catch (e) { /* keep current view */ }
    },
  };
}
window.reviewMixin = reviewMixin;
```

- [ ] **Step 2: Verify the lifecycle guardrail still holds**

Run: `.venv/bin/pytest tests/unit/test_htmx_alpine_single_lifecycle.py tests/unit/test_no_x_data_stack.py -q`
Expected: PASS (no `Alpine.initTree`/`htmx.process`, no `_x_dataStack` introduced).

- [ ] **Step 3: Commit**

```bash
git add backend/app/static/review.js
git commit -m "feat(review): data-driven reviewMixin (accept/delete/edit/accept-all/apply/walk)"
```

---

## Task 5: Draft card panel + review bar + CSS

**Files:**
- Modify: `backend/app/templates/pages/_anno_draft.html`
- Modify: `backend/app/static/app.css`

- [ ] **Step 1: Rewrite `_anno_draft.html` as Alpine cards**

Replace the entire contents of `backend/app/templates/pages/_anno_draft.html` with:

```html
{# Draft review panel (Alpine-data-driven). Reads draftMarkers / draftFields /
   draftNotes from the enclosing clip-detail x-data (player + reviewMixin).
   `review_mode` is passed by the route; tab state is shared with Published. #}
<template x-if="runError">
  <div class="anno-status error"><span x-text="`Failed: ${runError}`"></span></div>
</template>

<div class="review-bar">
  <button type="button" class="btn sm" @click="acceptAll()">✓ Accept all</button>
  <span class="review-kept" x-text="'✓ ' + acceptedCount() + '/' + totalCount()"></span>
  <span class="grow"></span>
  <button type="button" class="btn sm nav" :disabled="reviewPos() <= 1" @click="navClip(-1)" title="Previous clip">‹</button>
  <span class="review-pos" x-text="'Clip ' + reviewPos() + ' / ' + reviewLen()"></span>
  <button type="button" class="btn sm nav" :disabled="reviewPos() >= reviewLen()" @click="navClip(1)" title="Next clip">›</button>
  <button type="button" class="btn good sm" @click="applyDraft()" x-text="'Apply (' + acceptedCount() + ')'"></button>
</div>

<template x-if="totalCount() === 0">
  <p class="muted" style="padding:14px 2px">No proposals to review.</p>
</template>

<div class="edit-hint" x-show="totalCount() > 0">
  <span class="eh-ico">✎</span>
  <span>Read each proposal below. <b>Accept</b> marks it (yellow on the timeline);
    <b>Delete</b> removes it. On a marker, <b>Edit</b> then drag the highlighted
    bar's edges on the <b>timeline</b> to set in/out. The <b>‹ ›</b> arrows move
    between clips in the review set.</span>
</div>

<div class="anno-tabs" role="tablist" x-show="totalCount() > 0">
  <button type="button" class="anno-tab" :class="{ active: tab === 'markers' }" @click="tab = 'markers'">Markers <span class="count" x-text="draftMarkers.length"></span></button>
  <button type="button" class="anno-tab" :class="{ active: tab === 'fields' }" @click="tab = 'fields'">Fields <span class="count" x-text="draftFields.length"></span></button>
  <button type="button" class="anno-tab" :class="{ active: tab === 'notes' }" @click="tab = 'notes'">Notes <span class="count" x-text="draftNotes.length"></span></button>
</div>

<div class="anno-section" x-show="tab === 'markers'" x-cloak>
  <template x-for="m in draftMarkers" :key="m.item_id">
    <div class="ri-card ri-marker" :class="{ editing: editingItemId === m.item_id }">
      <div class="ri-card-top">
        <span class="ri-state" :class="m.status" x-text="m.status === 'accepted' ? 'Accepted' : 'Proposed'"></span>
        <span class="cat" x-show="m.category" x-text="m.category"></span>
        <span class="ri-tc" x-text="tc(m.in_secs) + (m.out_secs != null ? ' – ' + tc(m.out_secs) : '')"></span>
      </div>
      <div class="ri-name" @click="seek(m.in_secs)" x-text="m.name"></div>
      <div class="ri-desc" x-text="m.description"></div>
      <div class="ri-editor" x-show="editingItemId === m.item_id" x-cloak>
        <label class="field"><span class="field-label">Name</span><input class="txt" type="text" x-model="m.name" @change="_persistMarker(m.item_id)"></label>
        <label class="field"><span class="field-label">Category</span><input class="txt" type="text" x-model="m.category" @change="_persistMarker(m.item_id)"></label>
        <label class="field"><span class="field-label">Description</span><textarea class="txt-area" x-model="m.description" @change="_persistMarker(m.item_id)"></textarea></label>
        <div class="ri-time">
          <span class="ri-readout">in <b x-text="tc(m.in_secs)"></b> · out <b x-text="m.out_secs != null ? tc(m.out_secs) : '—'"></b></span>
          <span class="ri-hint">Drag the highlighted bar on the timeline · ←/→ nudge (Shift = 1 frame)</span>
        </div>
      </div>
      <div class="ri-actions">
        <button type="button" class="btn sm" :class="m.status === 'accepted' && 'good'" @click="toggleAccept(m)" x-text="m.status === 'accepted' ? '✓ Accepted' : 'Accept'"></button>
        <button type="button" class="btn sm ghost" @click="toggleEdit(m.item_id)" x-text="editingItemId === m.item_id ? 'Done' : '✎ Edit'"></button>
        <button type="button" class="btn sm danger" @click="del(m, $event)">Delete</button>
      </div>
    </div>
  </template>
</div>

<div class="anno-section" x-show="tab === 'fields'" x-cloak>
  <template x-for="f in draftFields" :key="f.item_id">
    <div class="ri-card" :class="{ editing: editingItemId === f.item_id }">
      <div class="ri-card-top">
        <span class="ri-state" :class="f.status" x-text="f.status === 'accepted' ? 'Accepted' : 'Proposed'"></span>
        <span class="ident" x-text="f.identifier"></span>
      </div>
      <div class="ri-value" x-text="f.value"></div>
      <div class="ri-editor" x-show="editingItemId === f.item_id" x-cloak>
        <label class="field"><span class="field-label" x-text="f.identifier"></span><input class="txt" type="text" x-model="f.value" @change="persistField(f)"></label>
      </div>
      <div class="ri-actions">
        <button type="button" class="btn sm" :class="f.status === 'accepted' && 'good'" @click="toggleAccept(f)" x-text="f.status === 'accepted' ? '✓ Accepted' : 'Accept'"></button>
        <button type="button" class="btn sm ghost" @click="toggleEdit(f.item_id)" x-text="editingItemId === f.item_id ? 'Done' : '✎ Edit'"></button>
        <button type="button" class="btn sm danger" @click="del(f, $event)">Delete</button>
      </div>
    </div>
  </template>
</div>

<div class="anno-section" x-show="tab === 'notes'" x-cloak>
  <template x-for="n in draftNotes" :key="n.item_id">
    <div class="ri-card ri-note-row" :class="{ editing: editingItemId === n.item_id }">
      <div class="ri-card-top">
        <span class="ri-state" :class="n.status" x-text="n.status === 'accepted' ? 'Accepted' : 'Proposed'"></span>
      </div>
      <div class="ri-note-text" x-text="n.text"></div>
      <div class="ri-editor" x-show="editingItemId === n.item_id" x-cloak>
        <label class="field"><span class="field-label">Note</span><textarea class="txt-area" style="min-height:90px" x-model="n.text" @change="persistNote(n)"></textarea></label>
      </div>
      <div class="ri-actions">
        <button type="button" class="btn sm" :class="n.status === 'accepted' && 'good'" @click="toggleAccept(n)" x-text="n.status === 'accepted' ? '✓ Accepted' : 'Accept'"></button>
        <button type="button" class="btn sm ghost" @click="toggleEdit(n.item_id)" x-text="editingItemId === n.item_id ? 'Done' : '✎ Edit'"></button>
        <button type="button" class="btn sm danger" @click="del(n, $event)">Delete</button>
      </div>
    </div>
  </template>
</div>
```

- [ ] **Step 2: Append the CSS**

Append to `backend/app/static/app.css` (the design's `.ri-*` / `.review-bar` / `.edit-hint` / 3-color timeline rules; tokens + one purple literal for "editing", matching the design):

```css
/* ─── Draft review (redesigned cards + bar) ──────────────────────────── */
.review-bar { display: flex; align-items: center; gap: 7px; padding: 8px 0 10px; border-bottom: 1px solid var(--line); margin-bottom: 10px; flex-wrap: wrap; row-gap: 8px; }
.review-bar .review-kept { color: var(--accent); font-size: 11.5px; font-family: var(--f-mono); }
.review-bar .review-pos { color: var(--text-2); font-size: 12px; font-family: var(--f-mono); min-width: 64px; text-align: center; }
.review-bar .btn.nav { width: 28px; padding: 0; font-size: 15px; }
.edit-hint { display: flex; gap: 7px; align-items: flex-start; font-size: 11.5px; color: var(--text-2); line-height: 1.5;
  background: color-mix(in oklab, var(--info) 10%, transparent);
  border: 1px solid color-mix(in oklab, var(--info) 26%, var(--line)); border-radius: var(--r-2); padding: 8px 10px; margin-bottom: 12px; }
.edit-hint b { color: var(--text); font-weight: 600; }
.edit-hint .eh-ico { color: var(--info); flex: none; }
.ri-card { border: 1px solid var(--line); border-radius: var(--r-2); background: var(--panel); padding: 10px 12px; margin-bottom: 8px;
  display: flex; flex-direction: column; gap: 6px; transition: border-color 120ms, box-shadow 120ms, opacity 120ms; }
.ri-card.editing { border-color: #9a6cff; box-shadow: 0 0 0 2px color-mix(in oklab, #9a6cff 30%, transparent); }
.ri-state { font-size: 9.5px; text-transform: uppercase; letter-spacing: 0.05em; padding: 1px 7px; border-radius: 999px; border: 1px solid var(--line-2); color: var(--text-3); white-space: nowrap; }
.ri-state.proposed { color: var(--info); border-color: color-mix(in oklab, var(--info) 35%, transparent); }
.ri-state.accepted { color: var(--accent); border-color: color-mix(in oklab, var(--accent) 40%, transparent); }
.ri-card-top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.ri-actions { display: flex; gap: 6px; justify-content: flex-end; flex-wrap: wrap; margin-top: 2px; }
.ri-actions .btn { flex: none; }
.ri-editor .txt, .ri-editor .txt-area { width: 100%; }
.ri-card-top .cat { font-size: 9.5px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--accent);
  border: 1px solid color-mix(in oklab, var(--accent) 32%, var(--line)); border-radius: 4px; padding: 1px 6px; }
.ri-card-top .ri-tc { color: var(--text-3); font-size: 11.5px; font-family: var(--f-mono); }
.ri-card-top .ident { color: var(--text-2); font-size: 11.5px; font-family: var(--f-mono); }
.ri-name { font-size: 13.5px; font-weight: 500; color: var(--text); line-height: 1.4; text-wrap: pretty; cursor: pointer; }
.ri-desc { font-size: 12px; color: var(--text-2); line-height: 1.5; text-wrap: pretty; }
.ri-value { font-size: 13px; color: var(--text); line-height: 1.45; word-break: break-word; }
.ri-note-text { font-size: 12.5px; color: var(--text); line-height: 1.55; text-wrap: pretty; }
.ri-editor { margin-top: 4px; padding-top: 9px; border-top: 1px dashed var(--line); display: flex; flex-direction: column; gap: 9px; }
.ri-editor .txt-area { min-height: 60px; }
.ri-time { display: flex; flex-direction: column; gap: 3px; }
.ri-readout { font-size: 12px; color: var(--text-2); font-family: var(--f-mono); }
.ri-readout b { color: var(--text); }
.ri-hint { font-size: 11px; color: var(--text-3); }
.timeline .range.draft-range { cursor: grab; background: color-mix(in oklab, var(--info) 50%, transparent); }
.timeline .range.draft-range.is-accepted { background: color-mix(in oklab, var(--accent) 60%, transparent); }
.timeline .range.draft-range.editing { background: color-mix(in oklab, #9a6cff 68%, transparent); outline: 1px solid #9a6cff; outline-offset: 1px; }
.timeline .range.draft-range:hover { filter: brightness(1.2); }
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/templates/pages/_anno_draft.html backend/app/static/app.css
git commit -m "feat(review): card-based draft panel + review bar + 3-color timeline CSS"
```

---

## Task 6: Wire `clip_detail.html` x-data + timeline rows + remove old bar

**Files:**
- Modify: `backend/app/templates/pages/clip_detail.html`
- Modify: `backend/app/routes/pages/clips.py` (pass `draft_arrays` to the page)

- [ ] **Step 1: Pass the draft arrays from the route**

In `backend/app/routes/pages/clips.py`, import the serializer (with the other `view_models` imports near the top):

```python
from backend.app.ui.view_models import clip_detail, clip_summary, draft_review_arrays
```

In `clip_detail_page` (where `ctx_dict["draft"] = await _build_draft_for_clip(ctx, clip_id)` is set), add the arrays right after:

```python
    ctx_dict["draft"] = await _build_draft_for_clip(ctx, clip_id)
    ctx_dict["draft_arrays"] = draft_review_arrays(ctx_dict["draft"])
```

- [ ] **Step 2: Update the clip-detail x-data + scope + markers-row x_show**

In `backend/app/templates/pages/clip_detail.html`:

(a) Replace the `x-data` object so the player gets the status-bearing markers, the draft field/note arrays + `reviewMixin` are merged, and scope honors `?scope=draft`:

```html
     x-data='Object.assign(
       player({{ clip.fps }}, {{ clip.duration_secs }}, {{ clip.markers|tojson }}, {{ draft_arrays.markers|tojson }}),
       clipAnnotate({{ clip.id }}, "{{ clip.kind }}"),
       reviewMixin({{ clip.id }}),
       { scope: "{{ 'draft' if review_mode else 'published' }}", tab: "{{ 'fields' if clip.kind == 'image' else 'markers' }}",
         draftFields: {{ draft_arrays.fields|tojson }}, draftNotes: {{ draft_arrays.notes|tojson }},
         liveSession: liveSession({{ clip.id }}, { inactivityS: {{ gemini_live_inactivity_s|default(60) }} })
       }
     )'
     x-init="_reviewInit(); if (new URLSearchParams(location.search).get('scope') === 'draft') scope = 'draft'"
     @keydown.window="handleKey($event)">
```

(b) In the `rows` config for the player, hide the published markers row in draft scope — change the `markers` row's `"x_show": None` to:

```python
            "x_show": "scope === 'published'",
```

(c) Remove the old `review-actionbar` block entirely (the `{% if draft.has_draft %}<div class="review-actionbar" x-data="reviewQueue(...)">…</div>{% endif %}`) — the review bar now lives inside `_anno_draft.html`.

(d) The draft aside include stays as-is:

```html
    <div class="anno-scoped" id="draft-aside" x-show="scope === 'draft'" x-cloak>
      {% include "pages/_anno_draft.html" %}
    </div>
```

- [ ] **Step 3: Verify the clip-detail render + guardrails**

Run: `.venv/bin/pytest tests/integration/test_routes_pages.py tests/integration/test_clip_detail_draft.py tests/unit/test_no_x_data_stack.py tests/unit/test_htmx_alpine_single_lifecycle.py tests/unit/test_templates_shared.py -q`
Expected: PASS — the clip detail still renders (now with the card panel); guardrails green. If `test_clip_detail_draft.py` asserts old `_anno_panels`/`ri-accept` markup, update those assertions to the new card markup (the behavior — draft items present, accept/apply available — is preserved).

- [ ] **Step 4: Commit**

```bash
git add backend/app/templates/pages/clip_detail.html backend/app/routes/pages/clips.py
git commit -m "feat(review): wire clip-detail to card draft panel + reviewMixin + draft-scope timeline"
```

---

## Task 7: 3-color timeline `is-accepted` + batches Review → seeds the walk

**Files:**
- Modify: `backend/app/templates/pages/_player_overlay.html`
- Modify: `backend/app/templates/pages/_batches_table.html`
- Modify: `backend/app/templates/pages/batches.html` (add `reviewBatch` to `batchesPage`)

- [ ] **Step 1: Add `is-accepted` to the draft range class (overlay)**

In `backend/app/templates/pages/_player_overlay.html`, the draft-range `:class` currently reads `:class="{ editing: editingItemId === {{ m.item_id }} }"`. Change it to also color accepted bars yellow (reads `status` off the live Alpine draft item; harmless for studio, whose draft items have no `status`):

```html
             :class="{ editing: editingItemId === {{ m.item_id }}, 'is-accepted': (_draftItem({{ m.item_id }}) || {}).status === 'accepted' }"
```

- [ ] **Step 2: Make the batches Review → seed the queue**

In `backend/app/templates/pages/_batches_table.html`, replace the Review → anchor:

```html
        {% if not b.running and b.reviewed < b.completed %}
        <a class="btn sm primary" href="{{ b.review_href }}">Review →</a>
        {% endif %}
```

with a button that seeds the walk queue then navigates:

```html
        {% if not b.running and b.reviewed < b.completed %}
        <button type="button" class="btn sm primary"
                @click="reviewBatch({{ b.job_ids|tojson }}, '{{ b.review_href }}')">Review →</button>
        {% endif %}
```

- [ ] **Step 3: Add `reviewBatch` to `batchesPage()`**

In `backend/app/templates/pages/batches.html`, add this method to the `batchesPage()` return object (next to `retryFailed`):

```javascript
      async reviewBatch(jobIds, fallbackHref) {
        try {
          const r = await fetch("/batches/review-queue?job_ids=" + jobIds.join(","));
          if (r.ok) {
            const ids = (await r.json()).clip_ids || [];
            if (ids.length) {
              sessionStorage.setItem("catdv:reviewQueue", JSON.stringify(ids));
              location.href = "/clips/" + ids[0] + "?review=1&scope=draft";
              return;
            }
          }
        } catch (e) { /* fall through to the server-computed first-clip href */ }
        location.href = fallbackHref;
      },
```

- [ ] **Step 4: Verify**

Run: `.venv/bin/pytest tests/integration/test_routes_batches.py tests/integration/test_studio_page.py tests/unit/test_templates_shared.py -q`
Expected: PASS — batches page still renders (Review → is now a button), and studio (which also includes `_player_overlay.html`) still renders with the augmented `:class`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/templates/pages/_player_overlay.html backend/app/templates/pages/_batches_table.html backend/app/templates/pages/batches.html
git commit -m "feat(review): yellow accepted timeline bars + batch Review→ seeds the clip walk"
```

---

## Task 8: Full verification + ADR

**Files:**
- Create: `docs/adr/0051-draft-review-redesign.md`
- Modify: `docs/decisions.md`

- [ ] **Step 1: Full suite + linters**

Run:
```bash
.venv/bin/pytest -q
.venv/bin/lint-imports
.venv/bin/ruff check backend tests
```
Expected: green except the known pre-existing `tests/integration/test_routes_review.py::test_clip_detail_draft_controls_show_without_review_flag` (fails on `main`; if THIS plan's changes alter the draft controls it asserts, update that test to the new card markup since it's the same behavior — otherwise leave it). Branch-changed files must be ruff-clean. Guardrails (`test_no_x_data_stack`, `test_htmx_alpine_single_lifecycle`, `test_templates_shared`, `test_clips_page_perf`, `test_batches_page_perf`) green.

- [ ] **Step 2: Write ADR 0051**

Last ADR is `0050-batches-new-batch-picker.md`. Create `docs/adr/0051-draft-review-redesign.md` (MADR-lite: `# 0051. Draft review redesign`, `**Date:** 2026-06-02`, `**Status:** Accepted`, `## Context` / `## Alternatives` / `## Decision` / `## Consequences`). Record:
- The Draft panel moved from server-rendered `_anno_panels.html` (DOM-checkbox `reviewQueue`) to **Alpine-data-driven cards** (`draftMarkers`/`draftFields`/`draftNotes` with `status`), so cards + the 3-color timeline share one reactive source of truth.
- **Delete = reject** (non-destructive); **Apply = apply + stay**, refreshing via the new `GET /api/review/clips/{id}/draft-data` rather than swapping server HTML into the reactive subtree.
- Reused: scope toggle, `player.js` marker drag-edit/`_persistMarker`, decision/apply API, the `sessionStorage` review queue; the batch **Review →** seeds it via `GET /batches/review-queue`.
- `draft_review_arrays` excludes rejected and derives `status` from `decision` (no schema change).

- [ ] **Step 3: Update the decisions index**

Add a row for ADR 0051 to the table in `docs/decisions.md`.

- [ ] **Step 4: Commit**

```bash
git add docs/adr/0051-draft-review-redesign.md docs/decisions.md
git commit -m "docs(review): ADR 0051 — Alpine-data-driven draft review redesign"
```

- [ ] **Step 5: Manual acceptance (controller/user — live server)**

Run the spec's 8 manual flows on a running server (`server-start`/`server-stop` skills): batch Review → first clip in Draft; accept / accept-all (yellow bars); marker Edit + timeline drag persists; Delete (reject) hides + survives reload; Apply + stay; ‹ › walk preserving draft scope; Published unchanged; single-clip review.

---

## Self-Review

**Spec coverage:**
- Readable Proposed/Accepted cards + Accept/Edit/Delete → Task 5 (`_anno_draft.html`) + Task 4 (`reviewMixin`). ✓
- Review bar (Accept all · count · ‹ › · Apply) → Task 5 markup + Task 4 methods. ✓
- 3-color timeline (proposed/accepted/editing) + published row hidden in draft → Task 7 (`is-accepted`) + Task 6 (markers-row `x_show`) + Task 5 CSS. ✓
- Inline edit; marker in/out via timeline drag → reuses `player.js` (`startMarkerDrag`/`_persistMarker`); field/note edits via `persistField`/`persistNote` (Task 4). ✓
- Delete = reject; status from decision; rejected excluded → Task 1 (`draft_review_arrays`) + Task 4 (`del`). ✓
- Apply = apply + stay + refresh → Task 4 (`applyDraft`/`refreshDraft`) + Task 3 (`draft-data`). ✓
- Clip-walk preserving draft scope → Task 4 (`navClip`) + Task 6 (`x-init` scope) ; queue seeded by Task 7 (batch Review →) reusing the existing `sessionStorage` queue. ✓
- Batch Review → seeds the walk → Task 2 (`pending_clip_ids_for_jobs` + `/batches/review-queue`) + Task 7 (`reviewBatch`). ✓
- Published unchanged → Task 6 keeps the published `_anno_panels.html` block. ✓
- ADR → Task 8. ✓

**Placeholder scan:** None. ADR number 0051 concrete (last is 0050). The only conditional is "update `test_clip_detail_draft.py`/`test_routes_review.py` assertions if they pin the old markup" — that's a real, bounded instruction (run them; adjust the markup assertions to the cards if they break) not a vague placeholder.

**Type/name consistency:** `draft_review_arrays(draft) -> {markers, fields, notes}` (Task 1) is consumed identically by `draft-data` (Task 3) and the clip-detail route (Task 6); item keys (`item_id`, `status`, marker `in_secs`/`out_secs`/`name`/`category`/`description`, field `identifier`/`value`, note `text`) match what `reviewMixin` (Task 4) and `_anno_draft.html` (Task 5) read, and what `player.js` already expects for `draftMarkers`. `reviewMixin(clipId)` (Task 4) is composed in the Task 6 `x-data` and its methods (`acceptAll`/`toggleAccept`/`del`/`toggleEdit`/`navClip`/`applyDraft`/`refreshDraft`/`persistField`/`persistNote`/`_reviewInit`/`reviewPos`/`reviewLen`/`acceptedCount`/`totalCount`) are exactly the ones `_anno_draft.html` calls. `_draftItem(id)` (player.js) + `.status` is what Task 7's overlay `:class` reads. `pending_clip_ids_for_jobs` / `/batches/review-queue` (Task 2) feed `reviewBatch` (Task 7).
