# Draft Review Edit/Accept UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Save/Cancel buffered editing in the Draft review panel, applied items leave Draft, deletes are recoverable (Undo toast + Deleted section), and the apply write-through race is fixed.

**Architecture:** Backend: `build_draft_view` passes `applied_at` through; `draft_review_arrays` partitions items into live / applied (counted) / deleted buckets. Frontend: `reviewMixin` (review.js) owns a snapshot-based edit transaction (`startEdit`/`saveEdit`/`cancelEdit`) and recoverable deletes; `player.js` drag/nudge stop persisting (they mutate the buffered item); all decision writes go through the one `_inflight`-tracked `_persist`, which `applyDraft` already awaits.

**Tech Stack:** FastAPI + Jinja2 + Alpine.js (no JS test runner — backend changes are TDD'd with pytest; frontend changes are guarded by existing file-scan tests + the spec's manual acceptance flows).

**Spec:** `docs/specs/2026-06-04-draft-review-edit-accept-ux-design.md`

**Working directory:** the `worktree-draft-review-ux` worktree. Run tests with `.venv/bin/python -m pytest`.

---

### Task 1: `build_draft_view` passes `applied_at` through

**Files:**
- Modify: `backend/app/services/draft_view.py`
- Test: `tests/unit/test_draft_view.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_draft_view.py` (the file already defines `_annotation()` and imports `ReviewItem`):

```python
def test_build_draft_view_passes_applied_at_through():
    ann = _annotation()
    items = [
        ReviewItem(
            annotation_id=42, catdv_clip_id=101, kind="marker",
            proposed_value={"name": "S", "in": {"secs": 1.0}},
            applied_at="2026-06-04T10:00:00",
        ),
        ReviewItem(
            annotation_id=42, catdv_clip_id=101, kind="field",
            target_identifier="x.y", proposed_value="v",
        ),
        ReviewItem(
            annotation_id=42, catdv_clip_id=101, kind="note",
            target_identifier="n", proposed_value="t",
            applied_at="2026-06-04T11:00:00",
        ),
    ]
    view = build_draft_view(annotation=ann, review_items=items)
    assert view["markers"][0]["applied_at"] == "2026-06-04T10:00:00"
    assert view["fields"][0]["applied_at"] is None
    assert view["note_items"][0]["applied_at"] == "2026-06-04T11:00:00"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_draft_view.py::test_build_draft_view_passes_applied_at_through -v`
Expected: FAIL with `KeyError: 'applied_at'`

- [ ] **Step 3: Implement**

In `backend/app/services/draft_view.py`:

In `_marker_from_review`, after `"decision": item.decision,` add:

```python
        "applied_at": item.applied_at,
```

In `_field_from_review`, after `"decision": item.decision,` add:

```python
        "applied_at": item.applied_at,
```

In `build_draft_view`'s `note_items` list comprehension, after `"decision": it.decision,` add:

```python
            "applied_at": it.applied_at,
```

- [ ] **Step 4: Run tests to verify pass**

Run: `.venv/bin/python -m pytest tests/unit/test_draft_view.py -v`
Expected: all PASS (existing equality test `test_build_draft_view_returns_empty_when_annotation_is_none` compares the no-draft dict, which has no item dicts — unaffected).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/draft_view.py tests/unit/test_draft_view.py
git commit -m "feat(review): build_draft_view passes applied_at through item dicts"
```

---

### Task 2: `draft_review_arrays` — applied items excluded + counted, rejected items in a `deleted` bucket

**Files:**
- Modify: `backend/app/ui/view_models.py:249-295` (`draft_review_arrays`)
- Test: `tests/unit/test_draft_review_arrays.py`

- [ ] **Step 1: Update + add tests**

Replace `tests/unit/test_draft_review_arrays.py` with:

```python
from backend.app.ui.view_models import draft_review_arrays


def _draft():
    return {
        "has_draft": True,
        "markers": [
            {"item_id": 1, "decision": "pending", "name": "A", "category": "est",
             "description": "d", "in_secs": 2.0, "out_secs": 5.0, "color": None,
             "kind": "marker", "applied_at": None},
            {
                "item_id": 2, "decision": "accepted", "name": "B", "category": None,
                "description": None, "in_secs": 8.0, "out_secs": None, "color": None,
                "kind": "marker", "applied_at": None,
            },
            {
                "item_id": 3, "decision": "rejected", "name": "C", "category": None,
                "description": None, "in_secs": 9.0, "out_secs": None, "color": None,
                "kind": "marker", "applied_at": None,
            },
        ],
        "fields": [
            {
                "item_id": 11, "decision": "pending", "identifier": "x.y",
                "value": "v", "multi": False, "kind": "field", "applied_at": None,
            },
        ],
        "note_items": [
            {"item_id": 21, "decision": "accepted", "identifier": None, "text": "note",
             "kind": "note", "applied_at": None},
            {"item_id": 22, "decision": "rejected", "identifier": None, "text": "gone",
             "kind": "note", "applied_at": None},
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


def test_rejected_items_land_in_deleted_bucket():
    a = draft_review_arrays(_draft())
    assert [m["item_id"] for m in a["deleted"]["markers"]] == [3]
    assert a["deleted"]["markers"][0]["name"] == "C"
    assert a["deleted"]["fields"] == []
    assert [n["item_id"] for n in a["deleted"]["notes"]] == [22]


def test_applied_items_excluded_from_arrays_and_counted():
    d = _draft()
    d["markers"][0]["applied_at"] = "2026-06-04T10:00:00"   # pending+applied
    d["fields"][0]["applied_at"] = "2026-06-04T10:00:00"    # pending+applied
    a = draft_review_arrays(d)
    assert [m["item_id"] for m in a["markers"]] == [2]
    assert a["fields"] == []
    assert a["applied_count"] == 2


def test_rejected_and_applied_items_appear_nowhere():
    d = _draft()
    d["markers"][2]["applied_at"] = "2026-06-04T10:00:00"   # rejected+applied
    a = draft_review_arrays(d)
    assert [m["item_id"] for m in a["markers"]] == [1, 2]
    assert a["deleted"]["markers"] == []
    assert a["applied_count"] == 0


def test_no_draft_returns_empty_arrays():
    assert draft_review_arrays({"has_draft": False}) == {
        "markers": [], "fields": [], "notes": [],
        "applied_count": 0,
        "deleted": {"markers": [], "fields": [], "notes": []},
    }
```

- [ ] **Step 2: Run to verify the new tests fail**

Run: `.venv/bin/python -m pytest tests/unit/test_draft_review_arrays.py -v`
Expected: `test_rejected_items_land_in_deleted_bucket`, `test_applied_items_excluded_from_arrays_and_counted`, `test_rejected_and_applied_items_appear_nowhere`, `test_no_draft_returns_empty_arrays` FAIL (`KeyError: 'deleted'` / dict mismatch); the first two still PASS.

- [ ] **Step 3: Implement**

Replace the body of `draft_review_arrays` in `backend/app/ui/view_models.py` (keep its docstring location; update the docstring as shown):

```python
def draft_review_arrays(draft: dict) -> dict:
    """Shape a `build_draft_view` result into the Alpine arrays the Draft
    panel + 3-color timeline render from. Each item carries `item_id` and a
    `status` ("accepted" if its review_item decision is accepted, else
    "proposed"). Partitioning: applied items (applied_at set) are excluded
    from the arrays and counted in `applied_count` (they're syncing to
    CatDV); rejected items land in the `deleted` bucket so the panel can
    offer Restore; rejected+applied items appear nowhere. Pure function —
    unit-tested in isolation."""
    out = {
        "markers": [],
        "fields": [],
        "notes": [],
        "applied_count": 0,
        "deleted": {"markers": [], "fields": [], "notes": []},
    }
    if not draft.get("has_draft"):
        return out

    def _status(decision) -> str:
        return "accepted" if decision == "accepted" else "proposed"

    def _marker(m: dict) -> dict:
        return {
            "item_id": m["item_id"],
            "status": _status(m.get("decision")),
            "name": m.get("name") or "",
            "category": m.get("category"),
            "description": m.get("description"),
            "in_secs": m["in_secs"],
            "out_secs": m.get("out_secs"),
            "color": m.get("color"),
        }

    def _field(f: dict) -> dict:
        return {
            "item_id": f["item_id"],
            "status": _status(f.get("decision")),
            "identifier": f.get("identifier") or "",
            "value": f.get("value", ""),
            "multi": bool(f.get("multi")),
        }

    def _note(n: dict) -> dict:
        return {
            "item_id": n["item_id"],
            "status": _status(n.get("decision")),
            "text": n.get("text") or "",
        }

    for src_key, out_key, shape in (
        ("markers", "markers", _marker),
        ("fields", "fields", _field),
        ("note_items", "notes", _note),
    ):
        for it in draft.get(src_key, []):
            rejected = it.get("decision") == "rejected"
            applied = it.get("applied_at") is not None
            if rejected and applied:
                continue  # gone upstream and unwanted: show nowhere
            if rejected:
                out["deleted"][out_key].append(shape(it))
            elif applied:
                out["applied_count"] += 1
            else:
                out[out_key].append(shape(it))
    return out
```

- [ ] **Step 4: Run tests to verify pass**

Run: `.venv/bin/python -m pytest tests/unit/test_draft_review_arrays.py -v`
Expected: all 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/ui/view_models.py tests/unit/test_draft_review_arrays.py
git commit -m "feat(review): draft arrays exclude applied items, expose applied_count + deleted bucket"
```

---

### Task 3: Integration — draft-data keys, reject→restore round-trip, applied count

**Files:**
- Test: `tests/integration/test_review_queue_and_draft_data.py`

No production code changes expected — this locks the route behavior built in Tasks 1–2.

- [ ] **Step 1: Update the keys assertion + add round-trip tests**

In `tests/integration/test_review_queue_and_draft_data.py`, replace `test_draft_data_route` with:

```python
def test_draft_data_route(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        asyncio.run(_seed(client.app.state.core_ctx))
        r = client.get("/api/review/clips/101/draft-data")
        assert r.status_code == 200
        body = r.json()
        assert set(body.keys()) == {"markers", "fields", "notes", "applied_count", "deleted"}
        # the seeded pending marker item is present as a "proposed" card
        assert any(m["status"] == "proposed" for m in body["markers"])
        assert body["applied_count"] == 0
        assert body["deleted"] == {"markers": [], "fields": [], "notes": []}


def test_reject_then_restore_round_trip(monkeypatch, tmp_path):
    """Delete (reject) moves the item into the deleted bucket; restoring it
    (decision=pending) moves it back into the live arrays."""
    with _make_client(monkeypatch, tmp_path) as client:
        asyncio.run(_seed(client.app.state.core_ctx))
        item_id = client.get("/api/review/clips/101/draft-data").json()["markers"][0]["item_id"]

        r = client.post(f"/api/review/items/{item_id}/decision", json={"decision": "rejected"})
        assert r.status_code == 200
        body = client.get("/api/review/clips/101/draft-data").json()
        assert body["markers"] == []
        assert [m["item_id"] for m in body["deleted"]["markers"]] == [item_id]

        r = client.post(f"/api/review/items/{item_id}/decision", json={"decision": "pending"})
        assert r.status_code == 200
        body = client.get("/api/review/clips/101/draft-data").json()
        assert [m["item_id"] for m in body["markers"]] == [item_id]
        assert body["markers"][0]["status"] == "proposed"
        assert body["deleted"]["markers"] == []


def test_applied_items_leave_draft_data(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        ctx = client.app.state.core_ctx
        asyncio.run(_seed(ctx))
        item_id = client.get("/api/review/clips/101/draft-data").json()["markers"][0]["item_id"]
        asyncio.run(ReviewItemsRepo().mark_applied(ctx.db, [item_id]))
        body = client.get("/api/review/clips/101/draft-data").json()
        assert body["markers"] == []
        assert body["applied_count"] == 1
        assert body["deleted"] == {"markers": [], "fields": [], "notes": []}
```

- [ ] **Step 2: Run the integration tests**

Run: `.venv/bin/python -m pytest tests/integration/test_review_queue_and_draft_data.py -v`
Expected: all PASS (Tasks 1–2 already implemented the behavior; if any FAIL, fix the view-model code, not the test).

- [ ] **Step 3: Run adjacent integration suites for regressions**

Run: `.venv/bin/python -m pytest tests/integration/test_routes_review.py tests/integration/test_clip_detail_draft.py tests/integration/test_studio_review_items_e2e.py -v`
Expected: all PASS. If a test asserts the old draft-data key set or old `draft_review_arrays` shape, update that assertion to the new shape (keys `applied_count` + `deleted` added) — the new shape is the spec'd contract.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_review_queue_and_draft_data.py
git commit -m "test(review): lock draft-data contract — deleted bucket, applied_count, restore round-trip"
```

---

### Task 4: `toast.js` — optional action button (Undo)

**Files:**
- Modify: `backend/app/static/toast.js`
- Modify: `backend/app/static/app.css` (append one rule)

No JS test runner exists; `tests/unit/test_toast_store_registered.py` only guards the layout include (unchanged). Verified via the manual flows.

- [ ] **Step 1: Implement the action option**

In `backend/app/static/toast.js`, replace `push` and add `runAction` after `push`:

```js
    push(message, opts = {}) {
      const level = opts.level || 'info';  // 'info' | 'success' | 'error'
      const ttlMs = opts.ttlMs ?? (level === 'error' ? 8000 : 4000);
      const id = this._nextId++;
      // action: { label, fn } — rendered as a button; fn runs once, then dismiss.
      this.items.push({ id, message, level, action: opts.action || null });
      this._render();
      setTimeout(() => this.dismiss(id), ttlMs);
    },

    runAction(id) {
      const t = this.items.find(t => t.id === id);
      if (t && t.action && typeof t.action.fn === 'function') t.action.fn();
      this.dismiss(id);
    },
```

And in `_render`, replace the template string with (action button between message and close; closures can't be serialized into innerHTML, hence `runAction(id)`):

```js
      root.innerHTML = this.items.map(t => `
        <div class="toast toast-${t.level}" data-toast-id="${t.id}">
          <span class="toast-msg">${escapeHtml(t.message)}</span>
          ${t.action ? `<button class="toast-action"
                  onclick="Alpine.store('toast').runAction(${t.id})">${escapeHtml(t.action.label)}</button>` : ''}
          <button class="toast-close" aria-label="Dismiss"
                  onclick="Alpine.store('toast').dismiss(${t.id})">×</button>
        </div>
      `).join('');
```

- [ ] **Step 2: Add the CSS**

In `backend/app/static/app.css`, directly after the `.toast-close` rules (search `toast-close`), append:

```css
#toast-root .toast-action {
  background: none;
  border: 1px solid var(--line-3);
  border-radius: 5px;
  color: var(--text);
  cursor: pointer;
  font: inherit;
  font-size: 11.5px;
  font-weight: 600;
  padding: 1px 9px;
  flex: none;
}
#toast-root .toast-action:hover { background: var(--hover); }
```

- [ ] **Step 3: Run the frontend guard tests**

Run: `.venv/bin/python -m pytest tests/unit/test_toast_store_registered.py tests/unit/test_no_x_data_stack.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/app/static/toast.js backend/app/static/app.css
git commit -m "feat(toast): optional action button on toasts (Undo support)"
```

---

### Task 5: `review.js` + `player.js` — buffered Save/Cancel edit, tracked persists, recoverable delete

**Files:**
- Modify: `backend/app/static/review.js`
- Modify: `backend/app/static/player.js:40-142, 261-264`

These two change together: review.js takes over all persistence; player.js stops persisting.

- [ ] **Step 1: Rewrite the edit/delete section of `review.js`**

Replace the whole `// ── accept / delete / edit ───` section (from `_inflight: new Set(),` through `persistNote(...)`) with:

```js
    // ── accept / delete / edit ───────────────────────────────────
    // In-flight decision POSTs. `applyDraft` awaits these before enqueuing the
    // upstream apply, so a freshly-accepted item can't be missed by a race
    // between the decision write and the apply read. EVERY decision write in
    // the panel must go through _persist for that guarantee to hold.
    _inflight: new Set(),
    _persist(item, decision, editedValue) {
      const body = { decision };
      if (editedValue !== undefined) body.edited_value = editedValue;
      const p = fetch(`/api/review/items/${item.item_id}/decision`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
        .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); })
        .catch(e => {
          Alpine.store("toast").push(`Decision not saved: ${e.message || e}`, { level: "error" });
        });
      this._inflight.add(p);
      p.finally(() => this._inflight.delete(p));
      return p;
    },
    acceptAll() {
      for (const it of this._allDraft()) {
        if (it.status !== "accepted") { it.status = "accepted"; this._persist(it, "accepted"); }
      }
    },
    // Locate a live draft item by id across the three kind arrays.
    _findDraft(itemId) {
      for (const [key, kind, bucket] of [
        ["draftMarkers", "marker", "markers"],
        ["draftFields", "field", "fields"],
        ["draftNotes", "note", "notes"],
      ]) {
        const it = this[key].find(x => x.item_id === itemId);
        if (it) return { it, key, kind, bucket };
      }
      return null;
    },
    // ── buffered edit: snapshot on open, persist on Save, revert on Cancel ──
    _editSnapshot: null,
    startEdit(itemId, opts = {}) {
      if (this.editingItemId === itemId) return;
      if (this.editingItemId != null) this.saveEdit();   // switching auto-saves
      const f = this._findDraft(itemId);
      if (!f) return;
      this._editSnapshot = JSON.parse(JSON.stringify(f.it));
      this.editingItemId = itemId;
      if (f.kind === "marker" && opts.seek !== false) this.seek(f.it.in_secs, { play: false });
    },
    cancelEdit() {
      const f = this.editingItemId != null ? this._findDraft(this.editingItemId) : null;
      if (f && this._editSnapshot) Object.assign(f.it, this._editSnapshot);
      this.editingItemId = null;
      this._editSnapshot = null;
    },
    // One tracked POST per Save. Markers send the full value shape: the
    // backend's COALESCE replaces edited_value wholesale, and write_queue
    // requires {name, in:{secs}} or the marker is silently dropped on apply.
    saveEdit() {
      const f = this.editingItemId != null ? this._findDraft(this.editingItemId) : null;
      this.editingItemId = null;
      this._editSnapshot = null;
      if (!f) return;
      const it = f.it;
      let edited;
      if (f.kind === "marker") {
        edited = {
          name: it.name || "",
          category: it.category != null ? it.category : null,
          description: it.description != null ? it.description : null,
          in: { secs: it.in_secs },
        };
        if (it.color != null) edited.color = it.color;
        if (it.out_secs != null) edited.out = { secs: it.out_secs };
      } else if (f.kind === "field") {
        edited = it.value;
      } else {
        edited = it.text;
      }
      it.status = "accepted";
      this._persist(it, "accepted", edited);
    },
    // ── delete (reject) + restore: nothing is unrecoverable ─────
    del(item, ev) {
      if (ev) ev.stopPropagation();
      const f = this._findDraft(item.item_id);
      if (!f) return;
      if (this.editingItemId === item.item_id) { this.editingItemId = null; this._editSnapshot = null; }
      this[f.key].splice(this[f.key].indexOf(f.it), 1);
      this.draftDeleted[f.bucket].push(f.it);
      this._persist(f.it, "rejected");
      Alpine.store("toast").push("Proposal deleted.", {
        level: "info", ttlMs: 6000,
        action: { label: "Undo", fn: () => this.restore(f.it) },
      });
    },
    restore(item) {
      for (const [bucket, key] of [
        ["markers", "draftMarkers"], ["fields", "draftFields"], ["notes", "draftNotes"],
      ]) {
        const i = this.draftDeleted[bucket].findIndex(x => x.item_id === item.item_id);
        if (i < 0) continue;
        const [it] = this.draftDeleted[bucket].splice(i, 1);
        it.status = "proposed";
        this[key].push(it);
        if (key === "draftMarkers") this[key].sort((a, b) => a.in_secs - b.in_secs);
        this._persist(it, "pending");
        return;
      }
    },
    deletedTotal() {
      const d = this.draftDeleted;
      return d.markers.length + d.fields.length + d.notes.length;
    },
```

- [ ] **Step 2: Make apply auto-save the open edit + refresh the new state**

Still in `review.js`, replace `acceptApplyAll` and `refreshDraft`:

```js
    // ── accept everything + apply, in one click ─────────────────
    // Auto-saves any open buffered edit first (otherwise it would be
    // silently dropped), accepts every still-visible proposal, waits for
    // the decision writes to land, then applies.
    async acceptApplyAll() {
      if (this.editingItemId != null) this.saveEdit();
      this.acceptAll();
      await this.applyDraft();
    },
```

```js
    async refreshDraft() {
      try {
        const r = await fetch(`/api/review/clips/${clipId}/draft-data`);
        if (!r.ok) return;
        const d = await r.json();
        // Replace arrays in place so player()'s draftMarkers ref stays bound.
        this.draftMarkers.splice(0, this.draftMarkers.length, ...d.markers);
        this.draftFields.splice(0, this.draftFields.length, ...d.fields);
        this.draftNotes.splice(0, this.draftNotes.length, ...d.notes);
        this.draftDeleted.markers.splice(0, this.draftDeleted.markers.length, ...d.deleted.markers);
        this.draftDeleted.fields.splice(0, this.draftDeleted.fields.length, ...d.deleted.fields);
        this.draftDeleted.notes.splice(0, this.draftDeleted.notes.length, ...d.deleted.notes);
        this.appliedCount = d.applied_count;
        this.editingItemId = null;
        this._editSnapshot = null;
      } catch (e) { /* keep current view */ }
    },
```

Also update the file-header comment (lines 1–6): replace
`// Persists via /api/review/items/{id}/decision and /clips/{id}/apply; marker`
`// in/out edits go through player.js::_persistMarker.` with
`// Persists via /api/review/items/{id}/decision and /clips/{id}/apply; ALL`
`// decision writes (incl. marker in/out edits) go through _persist so applyDraft`
`// can await them.`

- [ ] **Step 3: `player.js` — drag/nudge stop persisting, drag enters the buffered edit**

In `backend/app/static/player.js`:

1. In `startMarkerDrag`, replace `this.editingItemId = id;` with:

```js
      // Enter the buffered edit (snapshots for Cancel, auto-saves any other
      // open edit). reviewMixin provides startEdit on the clip-detail page;
      // fall back to the raw flag elsewhere.
      if (typeof this.startEdit === "function") this.startEdit(id, { seek: false });
      else this.editingItemId = id;
```

2. Replace `_endMarkerDrag` with (drag is preview-only now; Save persists):

```js
    _endMarkerDrag() {
      this._drag = null;
    },
```

3. In `nudgeMarker`, delete the line `this._persistMarker(this.editingItemId);`.

4. Delete the whole `_persistMarker(id) { ... }` method **and** the comment block above it (`// Persist the *whole* marker value: ...`).

5. Update the comment above `_drag: null` (lines 40–43): replace `then persists via the review decision endpoint.` with `persisting happens on Save via reviewMixin.saveEdit().`

- [ ] **Step 4: Verify no dangling references**

Run: `grep -rn "_persistMarker\|persistField\|persistNote\|toggleEdit" backend/app/static/ backend/app/templates/`
Expected: matches ONLY in `backend/app/templates/pages/_anno_draft.html` (removed in Task 6). If anything else matches, fix it before committing.

Run: `.venv/bin/python -m pytest tests/unit/test_no_x_data_stack.py tests/unit/test_htmx_alpine_single_lifecycle.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/static/review.js backend/app/static/player.js
git commit -m "feat(review): buffered save/cancel edit transaction; all decision writes tracked

Fixes the apply write-through race: marker edits previously persisted
fire-and-forget outside reviewMixin._inflight, so applyDraft could enqueue
before the decision landed."
```

---

### Task 6: Templates — Save/Cancel actions, deleted sections, post-apply empty state

**Files:**
- Modify: `backend/app/templates/pages/_anno_draft.html`
- Modify: `backend/app/templates/pages/clip_detail.html:14-17`
- Modify: `backend/app/static/app.css` (append deleted-section rules)

- [ ] **Step 1: Seed the new Alpine state in `clip_detail.html`**

In the `x-data` literal (the `Object.assign(...)` 4th argument), after
`draftFields: {{ draft_arrays.fields|tojson }}, draftNotes: {{ draft_arrays.notes|tojson }},` add:

```html
         draftDeleted: {{ draft_arrays.deleted|tojson }}, appliedCount: {{ draft_arrays.applied_count }},
```

- [ ] **Step 2: Rework `_anno_draft.html`**

Apply these edits:

1. **Empty state** — replace

```html
<template x-if="totalCount() === 0">
  <p class="muted" style="padding:14px 2px">No proposals to review.</p>
</template>
```

with

```html
<template x-if="totalCount() === 0 && appliedCount > 0">
  <p class="muted" style="padding:14px 2px"
     x-text="appliedCount + ' proposal' + (appliedCount === 1 ? '' : 's') + ' applied — syncing to CatDV; visible under Published once synced.'"></p>
</template>
<template x-if="totalCount() === 0 && appliedCount === 0 && deletedTotal() === 0">
  <p class="muted" style="padding:14px 2px">No proposals to review.</p>
</template>
```

2. **Hint text** — replace the inner `<span>` of `.edit-hint` with:

```html
  <span>Review each proposal below. <b>Delete</b> the ones you don't want
    (<b>Undo</b> or <b>Restore</b> brings them back); on a marker, <b>Edit</b>,
    drag the highlighted bar's edges on the <b>timeline</b> to set in/out, then
    <b>Save</b> (or <b>Cancel</b> to revert). <b>Accept &amp; apply all</b> writes
    everything still listed to CatDV. The <b>‹ ›</b> arrows move between clips
    in the review set.</span>
```

3. **Tabs visibility** — deleted items must stay reachable when every live proposal is deleted. Change the two `x-show="totalCount() > 0"` (on `.edit-hint` and `.anno-tabs`) to:

- `.edit-hint`: keep `x-show="totalCount() > 0"` (hint is about live proposals).
- `.anno-tabs`: `x-show="totalCount() > 0 || deletedTotal() > 0"`.

4. **Marker editor inputs** — remove the persist-on-change handlers; bindings stay:

```html
        <label class="field"><span class="field-label">Name</span><input class="txt" type="text" x-model="m.name"></label>
        <label class="field"><span class="field-label">Category</span><input class="txt" type="text" x-model="m.category"></label>
        <label class="field"><span class="field-label">Description</span><textarea class="txt-area" x-model="m.description"></textarea></label>
```

Same for the field editor (`<input class="txt" type="text" x-model="f.value">`, drop `@change="persistField(f)"`) and the note editor (`<textarea ... x-model="n.text"></textarea>`, drop `@change="persistNote(n)"`).

5. **Actions rows** — replace the marker card's `.ri-actions` with:

```html
      <div class="ri-actions" @click.stop>
        <button type="button" class="btn good sm" x-show="editingItemId === m.item_id" @click="saveEdit()">Save</button>
        <button type="button" class="btn sm ghost" x-show="editingItemId === m.item_id" @click="cancelEdit()">Cancel</button>
        <button type="button" class="btn sm ghost" x-show="editingItemId !== m.item_id" @click="startEdit(m.item_id)">✎ Edit</button>
        <button type="button" class="btn sm danger" x-show="editingItemId !== m.item_id" @click="del(m, $event)">Delete</button>
      </div>
```

Field card (`.ri-actions`, no `@click.stop` today — keep as-is structurally, swap buttons):

```html
      <div class="ri-actions">
        <button type="button" class="btn good sm" x-show="editingItemId === f.item_id" @click="saveEdit()">Save</button>
        <button type="button" class="btn sm ghost" x-show="editingItemId === f.item_id" @click="cancelEdit()">Cancel</button>
        <button type="button" class="btn sm ghost" x-show="editingItemId !== f.item_id" @click="startEdit(f.item_id)">✎ Edit</button>
        <button type="button" class="btn sm danger" x-show="editingItemId !== f.item_id" @click="del(f, $event)">Delete</button>
      </div>
```

Note card:

```html
      <div class="ri-actions">
        <button type="button" class="btn good sm" x-show="editingItemId === n.item_id" @click="saveEdit()">Save</button>
        <button type="button" class="btn sm ghost" x-show="editingItemId === n.item_id" @click="cancelEdit()">Cancel</button>
        <button type="button" class="btn sm ghost" x-show="editingItemId !== n.item_id" @click="startEdit(n.item_id)">✎ Edit</button>
        <button type="button" class="btn sm danger" x-show="editingItemId !== n.item_id" @click="del(n, $event)">Delete</button>
      </div>
```

6. **Deleted sections** — inside each `.anno-section`, after the closing `</template>` of the `x-for`, add (markers shown; fields use `draftDeleted.fields` + `x-text="f.identifier + ': ' + f.value"` with `x-for="f in ..."`; notes use `draftDeleted.notes` + `x-text="n.text"` with `x-for="n in ..."`):

```html
  <details class="ri-deleted" x-show="draftDeleted.markers.length > 0">
    <summary x-text="'Deleted (' + draftDeleted.markers.length + ')'"></summary>
    <template x-for="m in draftDeleted.markers" :key="m.item_id">
      <div class="ri-del-row">
        <span class="ri-del-label" x-text="(m.name || '—') + ' · ' + tc(m.in_secs)"></span>
        <button type="button" class="btn sm ghost" @click="restore(m)">Restore</button>
      </div>
    </template>
  </details>
```

- [ ] **Step 3: Add the deleted-section CSS**

In `backend/app/static/app.css`, near the `.ri-card` / `.ri-actions` rules (search `ri-actions`), append:

```css
/* Deleted-proposal recovery strip (draft review) */
.ri-deleted { margin-top: 10px; }
.ri-deleted summary {
  cursor: pointer;
  color: var(--text-3);
  font-size: 11.5px;
  user-select: none;
}
.ri-del-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 2px;
  border-bottom: 1px solid var(--line);
}
.ri-del-row:last-child { border-bottom: none; }
.ri-del-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-2);
  font-size: 12px;
}
```

- [ ] **Step 4: Run the template/frontend guard tests + page-render integration tests**

Run: `.venv/bin/python -m pytest tests/unit/test_no_x_data_stack.py tests/unit/test_templates_shared.py tests/unit/test_htmx_alpine_single_lifecycle.py tests/integration/test_clip_detail_draft.py tests/integration/test_clips_page_perf.py -v`
Expected: all PASS.

Then re-run the dangling-reference check from Task 5 Step 4:
`grep -rn "_persistMarker\|persistField\|persistNote\|toggleEdit" backend/app/static/ backend/app/templates/`
Expected: NO matches.

- [ ] **Step 5: Commit**

```bash
git add backend/app/templates/pages/_anno_draft.html backend/app/templates/pages/clip_detail.html backend/app/static/app.css
git commit -m "feat(review): save/cancel edit actions, deleted-proposals restore strip, post-apply empty state"
```

---

### Task 7: Full verification + spec status

**Files:**
- Modify: `docs/specs/2026-06-04-draft-review-edit-accept-ux-design.md` (status line)

- [ ] **Step 1: Full test suite**

Run: `.venv/bin/python -m pytest -q`
Expected: everything passes (baseline was 1098 passed, 4 skipped; new total higher).

- [ ] **Step 2: Import-linter contracts**

Run: `.venv/bin/lint-imports`
Expected: all contracts kept.

- [ ] **Step 3: Update spec status + commit**

Change `**Status:** Approved (design)` to `**Status:** Implemented` in the spec.

```bash
git add docs/specs/2026-06-04-draft-review-edit-accept-ux-design.md
git commit -m "docs(spec): mark draft review edit/accept UX spec implemented"
```

- [ ] **Step 4: Manual acceptance flows**

The spec's 7 manual flows need a running app + clips with draft proposals.
Per the user's verification-sequencing preference: read-only checks are fine
anytime, but flows that WRITE to CatDV (flow 3's apply, flow 4's sync) must
be deferred to a final pass coordinated with the user (CatDV seat limit).
Flows 1, 2, 5, 6 only write to the local DB — safe to run against a dev
server with the user's go-ahead. Report which flows were checked and which
are deferred.
