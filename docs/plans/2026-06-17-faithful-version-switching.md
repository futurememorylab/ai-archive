# Faithful Version Switching + History UI Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make "Make live" on an older clip version faithfully reproduce that version's marker set — removing markers we added in later versions, preserving markers we never authored (human/pre-existing), at the clip's real fps — and fix the clip-detail header (clipped dropdown + duplicate "History" labels).

**Architecture:** Add one writeback op, `ReconcileMarkers(desired, drop_secs)`, handled in `build_put_payload` at drain time where the live clip and its real fps are known. `PublishService.reactivate` computes `drop_secs` across all versions and emits it (with an fps sentinel so frames derive from the clip's real fps). Two small front-end changes round it out.

**Tech Stack:** Python 3.12+, FastAPI, aiosqlite, pytest (`asyncio_mode=auto`), Jinja2 templates, Alpine.js, plain CSS. Run tests with `.venv/bin/python -m pytest`.

**Spec:** `docs/specs/2026-06-17-faithful-version-switching-design.md`

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `backend/app/archive/model.py` | ChangeOp dataclasses + union | Add `ReconcileMarkers` |
| `backend/app/archive/change_set_json.py` | ChangeOp ↔ JSON (`op_json` round-trip) | Add `ReconcileMarkers` branch |
| `backend/app/archive/providers/catdv/payload.py` | Build the CatDV PUT body | Handle `ReconcileMarkers` (drop ours, keep foreign, fps-correct) |
| `backend/app/services/publish_service.py` | Publish + reactivate | Replace `_ops_from_snapshot` with `_switch_ops(target, versions)`; emit `ReconcileMarkers` |
| `backend/app/templates/sync_drawer.html` | Sync drawer op labels | Add `ReconcileMarkers` label |
| `backend/app/templates/pages/_clip_history_menu.html` | Version dropdown | Rename trigger "History" → "Versions" |
| `backend/app/templates/pages/_anno_panels.html` | Panel sub-tabs | Rename tab "History" → "Live sessions" |
| `backend/app/static/app.css` | Layout | Wrap `.anno-scope-row` so the version controls never clip |
| `docs/adr/0101-*.md`, `docs/decisions.md` | Decision record | New ADR + index row |

Tests touched/created: `tests/unit/test_change_set_json.py`, `tests/unit/test_catdv_payload.py`, `tests/integration/test_publish_service.py`, `tests/unit/test_version_switch_fidelity.py` (new).

---

## Task 1: Add the `ReconcileMarkers` op (model + JSON round-trip)

**Files:**
- Modify: `backend/app/archive/model.py` (after `AddMarkers`, ~line 69-75; union ~line 95)
- Modify: `backend/app/archive/change_set_json.py` (imports ~line 12-21; `change_op_to_dict` ~line 59-71; `change_op_from_dict` ~line 74-84)
- Test: `tests/unit/test_change_set_json.py`

- [ ] **Step 1: Write the failing round-trip test**

Add to `tests/unit/test_change_set_json.py` (top imports already include `change_op_to_json`/`change_op_from_json`, `Marker`, `Timecode`; add `ReconcileMarkers` to the `backend.app.archive.model` import):

```python
def test_reconcile_markers_round_trips():
    op = ReconcileMarkers(
        desired=(Marker(name="A", in_=Timecode(secs=4.0, fps=0.0), out=None),),
        drop_secs=(8.0, 12.0),
    )
    back = change_op_from_json(change_op_to_json(op))
    assert back == op
    assert isinstance(back, ReconcileMarkers)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_change_set_json.py::test_reconcile_markers_round_trips -v`
Expected: FAIL with `ImportError` / `NameError: ReconcileMarkers`.

- [ ] **Step 3: Add the dataclass to `model.py`**

Insert immediately after the `AddMarkers` class (before `class SetField`):

```python
@dataclass(frozen=True)
class ReconcileMarkers:
    """Reconcile the clip's marker set to a published version's snapshot — the
    'Make live' / switch path. Unlike AddMarkers (additive), this REMOVES the
    markers the app authored in OTHER versions, while preserving markers it
    never authored (pre-existing or human-added directly in CatDV).

    desired   — markers that must be present (the target version's). Built with a
                Timecode fps sentinel of 0.0 so the frame is derived from the
                clip's real fps at payload-build time, never a hardcoded value.
    drop_secs — in-point seconds of markers WE authored in other versions that
                must be removed; matched to the clip's frames at the clip's real
                fps in build_put_payload.
    """

    desired: tuple[Marker, ...]
    drop_secs: tuple[float, ...]

    def __post_init__(self) -> None:
        if isinstance(self.desired, list):
            object.__setattr__(self, "desired", tuple(self.desired))
        if isinstance(self.drop_secs, list):
            object.__setattr__(self, "drop_secs", tuple(self.drop_secs))
```

Update the union line:

```python
ChangeOp = AddMarkers | ReconcileMarkers | SetField | AppendNote | ReplaceNote
```

- [ ] **Step 4: Add the JSON branch to `change_set_json.py`**

Add `ReconcileMarkers` to the `from backend.app.archive.model import (...)` block. In `change_op_to_dict`, add before the `raise TypeError`:

```python
    if isinstance(op, ReconcileMarkers):
        return {
            "kind": "ReconcileMarkers",
            "desired": [_marker_to_dict(m) for m in op.desired],
            "drop_secs": list(op.drop_secs),
        }
```

In `change_op_from_dict`, add before the `raise ValueError`:

```python
    if k == "ReconcileMarkers":
        return ReconcileMarkers(
            desired=tuple(_marker_from_dict(m) for m in d["desired"]),
            drop_secs=tuple(float(s) for s in d.get("drop_secs", [])),
        )
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_change_set_json.py -v`
Expected: PASS (all tests, including the new one).

- [ ] **Step 6: Commit**

```bash
git add backend/app/archive/model.py backend/app/archive/change_set_json.py tests/unit/test_change_set_json.py
git commit -m "feat(versions): add ReconcileMarkers change-op (switch semantics) + JSON round-trip

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: `build_put_payload` handles `ReconcileMarkers`

**Files:**
- Modify: `backend/app/archive/providers/catdv/payload.py` (import ~line 10; marker block ~line 43-69)
- Test: `tests/unit/test_catdv_payload.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_catdv_payload.py` (add `ReconcileMarkers` to the `backend.app.archive.model` import at the top):

```python
def test_reconcile_drops_our_later_markers_keeps_foreign():
    # Clip carries our A,B,C,D (25fps) plus a human marker H we never authored.
    existing = [
        {"name": "A", "in": {"frm": 100, "fmt": 25.0, "secs": 4.0}},
        {"name": "B", "in": {"frm": 200, "fmt": 25.0, "secs": 8.0}},
        {"name": "C", "in": {"frm": 300, "fmt": 25.0, "secs": 12.0}},
        {"name": "D", "in": {"frm": 400, "fmt": 25.0, "secs": 16.0}},
        {"name": "H", "in": {"frm": 500, "fmt": 25.0, "secs": 20.0}},
    ]
    op = ReconcileMarkers(
        desired=(
            Marker(name="A", in_=Timecode(secs=4.0, fps=0.0), out=None),
            Marker(name="B", in_=Timecode(secs=8.0, fps=0.0), out=None),
        ),
        drop_secs=(12.0, 16.0),
    )
    payload = build_put_payload(current=_clip(markers=existing), ops=[op])
    assert sorted(m["name"] for m in payload["markers"]) == ["A", "B", "H"]


def test_reconcile_overwrites_our_copy_at_shared_frame():
    existing = [{"name": "MÃÃsto", "in": {"frm": 100, "fmt": 25.0, "secs": 4.0}}]
    op = ReconcileMarkers(
        desired=(Marker(name="Město", in_=Timecode(secs=4.0, fps=0.0), out=None),),
        drop_secs=(),
    )
    payload = build_put_payload(current=_clip(markers=existing), ops=[op])
    assert len(payload["markers"]) == 1
    assert payload["markers"][0]["name"] == "Město"


def test_reconcile_derives_frames_at_clip_fps_no_duplicate():
    # 30fps clip: our A,B already at frm 120/240 (4s,8s * 30). Re-asserting must
    # re-derive at 30fps (not 25) so no duplicate at frm 100/200 appears.
    existing = [
        {"name": "A", "in": {"frm": 120, "fmt": 30.0, "secs": 4.0}},
        {"name": "B", "in": {"frm": 240, "fmt": 30.0, "secs": 8.0}},
    ]
    op = ReconcileMarkers(
        desired=(
            Marker(name="A", in_=Timecode(secs=4.0, fps=0.0), out=None),
            Marker(name="B", in_=Timecode(secs=8.0, fps=0.0), out=None),
        ),
        drop_secs=(),
    )
    payload = build_put_payload(current=_clip(markers=existing, fps=30.0), ops=[op])
    assert sorted(m["in"]["frm"] for m in payload["markers"]) == [120, 240]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_catdv_payload.py -k reconcile -v`
Expected: FAIL — `ReconcileMarkers` ops are ignored, so `payload` has no/wrong `markers`.

- [ ] **Step 3: Replace the marker block in `payload.py`**

Add `ReconcileMarkers` to the `from backend.app.archive.model import (...)` block. Replace the existing `marker_ops = ...` block (the `if marker_ops:` section) with:

```python
    ops_list = list(ops)
    add_ops = [o for o in ops_list if isinstance(o, AddMarkers)]
    reconcile_ops = [o for o in ops_list if isinstance(o, ReconcileMarkers)]
    if add_ops or reconcile_ops:
        # CatDV replaces the markers array wholesale on PUT, so `working` is the
        # final set we send. Start from the live clip's markers and apply ops.
        working = list(current.get("markers") or [])
        # AddMarkers (additive publish-forward): our markers win on a same-frm
        # conflict (anti-mojibake); markers we don't touch are preserved.
        if add_ops:
            new_markers: list[dict[str, Any]] = []
            new_frms: set[int] = set()
            for op in add_ops:
                for marker in op.markers:
                    raw = marker_to_catdv(marker, fps)
                    frm = _in_frm(raw)
                    if frm is not None and frm in new_frms:
                        continue
                    new_markers.append(raw)
                    if frm is not None:
                        new_frms.add(frm)
            working = [m for m in working if _in_frm(m) not in new_frms] + new_markers
        # ReconcileMarkers (switch / Make-live): drop OUR other-version markers
        # (drop_frm) and re-assert the target's (desired), but KEEP markers we
        # never authored (pre-existing / human). Frames derive from the clip's
        # real fps via the Timecode fps=0.0 sentinel.
        for op in reconcile_ops:
            desired = [marker_to_catdv(m, fps) for m in op.desired]
            desired_frm = {_in_frm(m) for m in desired}
            drop_frm = {round(s * fps) for s in op.drop_secs}
            working = [
                m
                for m in working
                if _in_frm(m) not in drop_frm and _in_frm(m) not in desired_frm
            ] + desired
        payload["markers"] = working
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_catdv_payload.py -v`
Expected: PASS — the three new tests plus all existing `test_add_markers_*` (additive path unchanged).

- [ ] **Step 5: Commit**

```bash
git add backend/app/archive/providers/catdv/payload.py tests/unit/test_catdv_payload.py
git commit -m "feat(versions): build_put_payload reconciles markers on switch (drop ours, keep foreign, real fps)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: `reactivate` emits `ReconcileMarkers`

**Files:**
- Modify: `backend/app/services/publish_service.py` (import ~line 27; `reactivate` ~line 110-147; replace `_ops_from_snapshot` ~line 150-168)
- Test: `tests/integration/test_publish_service.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/integration/test_publish_service.py`:

```python
@pytest.mark.asyncio
async def test_reactivate_reconciles_markers_drops_later_versions(db):
    """Switching to v1 emits a ReconcileMarkers asserting v1's markers and
    dropping the markers only later versions added — no new version row."""
    from backend.app.archive.change_set_json import change_op_from_json
    from backend.app.archive.model import ReconcileMarkers
    from backend.app.models.annotation import ClipVersion

    repo = ClipVersionsRepo()
    v1 = await repo.insert(
        db,
        ClipVersion(
            catdv_clip_id=1, version_num=1,
            snapshot={"markers": [{"name": "A", "in": {"secs": 4.0}}], "fields": {}, "notes": None},
            origin="publish", publish_state="superseded",
        ),
    )
    await repo.insert(
        db,
        ClipVersion(
            catdv_clip_id=1, version_num=2,
            snapshot={
                "markers": [{"name": "A", "in": {"secs": 4.0}}, {"name": "B", "in": {"secs": 8.0}}],
                "fields": {}, "notes": None,
            },
            origin="publish", publish_state="live",
        ),
    )

    rid = await _svc().reactivate(db, clip_id=1, version_num=1)
    assert rid == v1
    assert len(await repo.list_by_clip(db, 1)) == 2  # no new version

    rows = await PendingOperationsRepo().list_pending_for_clip(
        db, provider_id="catdv", provider_clip_id="1"
    )
    recon = [
        o
        for o in (change_op_from_json(r["op_json"]) for r in rows)
        if isinstance(o, ReconcileMarkers)
    ]
    assert len(recon) == 1
    assert {m.name for m in recon[0].desired} == {"A"}
    assert set(recon[0].drop_secs) == {8.0}  # B (added in v2) is dropped
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/integration/test_publish_service.py::test_reactivate_reconciles_markers_drops_later_versions -v`
Expected: FAIL — current code emits `AddMarkers`, not `ReconcileMarkers`.

- [ ] **Step 3: Update imports and `reactivate` in `publish_service.py`**

Change the model import line from:

```python
from backend.app.archive.model import AddMarkers, ChangeOp, ReplaceNote, SetField
```

to:

```python
from backend.app.archive.model import ChangeOp, ReconcileMarkers, ReplaceNote, SetField
```

Replace the body of `reactivate` (from `ops = _ops_from_snapshot(...)` through the `enqueue_apply` call) so it builds ops via the new `_switch_ops`:

```python
        versions = await self._versions.list_by_clip(conn, clip_id)
        target = next((v for v in versions if v.version_num == version_num), None)
        if target is None:
            raise LookupError(f"clip {clip_id} has no version {version_num}")
        if target.publish_state == "live":
            return target.id  # already live — no-op

        ops = _switch_ops(target, versions)
        await self._versions.mark_publishing(conn, target.id)
        if not ops:
            # Nothing to assert and nothing to drop — it's live in effect.
            await self._versions.mark_live(conn, target.id)
            return target.id
        await self._wq.enqueue_apply(
            conn,
            clip_key=("catdv", str(clip_id)),
            items=[],
            target_map=TargetMap({}),
            expected_etag=None,
            annotation_id=target.annotation_id,
            fps=DEFAULT_FPS,
            clip_version_id=target.id,
            extra_ops=ops,
        )
        return target.id
```

- [ ] **Step 4: Replace `_ops_from_snapshot` with `_switch_ops`**

Delete the entire `_ops_from_snapshot` function and add:

```python
def _switch_ops(target, versions) -> list[ChangeOp]:
    """Ops that switch a clip to `target`'s snapshot (the 'Make live' path).

    Markers are reconciled, not merely added: we re-assert the target's markers
    and drop the markers WE authored in other versions, while preserving markers
    we never authored (handled in build_put_payload). drop_secs is the union of
    every version's marker in-seconds minus the target's, so only our own
    later/other additions are removed. Fields/notes overwrite to the target's
    values and are not cleared when absent (never destroy a foreign value).
    """
    snap = target.snapshot
    desired: list[Marker] = []
    target_secs: set[float] = set()
    for m in snap.get("markers") or []:
        if isinstance(m, dict):
            # fps=0.0 sentinel: the frame is derived from the clip's REAL fps in
            # build_put_payload, never a hardcoded value.
            mm = _marker_from_review_value(m, 0.0)
            if mm is not None:
                desired.append(mm)
                s = (m.get("in") or {}).get("secs")
                if isinstance(s, (int, float)):
                    target_secs.add(float(s))

    ours_all: set[float] = set()
    for v in versions:
        for m in v.snapshot.get("markers") or []:
            if isinstance(m, dict):
                s = (m.get("in") or {}).get("secs")
                if isinstance(s, (int, float)):
                    ours_all.add(float(s))
    drop_secs = tuple(sorted(ours_all - target_secs))

    ops: list[ChangeOp] = []
    # Always emit when there is something to assert OR drop, so switching to a
    # marker-less version still strips our later additions.
    if desired or drop_secs:
        ops.append(ReconcileMarkers(desired=tuple(desired), drop_secs=drop_secs))
    for ident, val in (snap.get("fields") or {}).items():
        ops.append(SetField(identifier=ident, value=val))
    if snap.get("notes"):
        ops.append(ReplaceNote(target="notes", text=str(snap["notes"])))
    if snap.get("bigNotes"):
        ops.append(ReplaceNote(target="bigNotes", text=str(snap["bigNotes"])))
    return ops
```

Add the import for `Marker` and `_marker_from_review_value` at the top of `publish_service.py` if not already present — `_marker_from_review_value` is already imported from `write_queue`; add `Marker`:

```python
from backend.app.archive.model import ChangeOp, Marker, ReconcileMarkers, ReplaceNote, SetField
```

- [ ] **Step 5: Run the test plus the existing reactivate test**

Run: `.venv/bin/python -m pytest tests/integration/test_publish_service.py -v`
Expected: PASS — the new test, plus `test_reactivate_enqueues_snapshot_and_creates_no_new_version` (its v1/v2 snapshots have empty markers, so only the `SetField(genre)` op is emitted — unchanged) and `test_publish_*`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/publish_service.py tests/integration/test_publish_service.py
git commit -m "feat(versions): reactivate reconciles markers (drop our later additions, real fps)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: End-to-end fidelity dry-run (offline; human marker survives)

**Files:**
- Create: `tests/unit/test_version_switch_fidelity.py`

- [ ] **Step 1: Write the test (snapshot → `_switch_ops` → `build_put_payload`)**

```python
# tests/unit/test_version_switch_fidelity.py
"""Offline proof of the full switch path: a version's snapshot, through
_switch_ops, through build_put_payload, against a live clip that also carries a
human-added marker. The human marker must survive; our later markers must go."""
from backend.app.archive.providers.catdv.payload import build_put_payload
from backend.app.models.annotation import ClipVersion
from backend.app.services.publish_service import _switch_ops


def _ver(num, markers, state):
    return ClipVersion(
        catdv_clip_id=1,
        version_num=num,
        snapshot={"markers": markers, "fields": {}, "notes": None},
        origin="publish",
        publish_state=state,
    )


def test_switch_preserves_human_marker_and_drops_later_ours():
    v1 = _ver(1, [{"name": "A", "in": {"secs": 4.0}}], "superseded")
    v2 = _ver(
        2,
        [{"name": "A", "in": {"secs": 4.0}}, {"name": "B", "in": {"secs": 8.0}}],
        "live",
    )
    ops = _switch_ops(v1, [v2, v1])

    current = {
        "ID": 1,
        "fps": 25.0,
        "fields": {},
        "markers": [
            {"name": "A", "in": {"frm": 100, "fmt": 25.0, "secs": 4.0}},
            {"name": "B", "in": {"frm": 200, "fmt": 25.0, "secs": 8.0}},
            {"name": "HUMAN", "in": {"frm": 999, "fmt": 25.0, "secs": 39.96}},
        ],
    }
    payload = build_put_payload(current=current, ops=ops)
    assert sorted(m["name"] for m in payload["markers"]) == ["A", "HUMAN"]
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_version_switch_fidelity.py -v`
Expected: PASS (Tasks 2 + 3 already implement the behaviour; this is the integration guard).

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_version_switch_fidelity.py
git commit -m "test(versions): end-to-end switch fidelity — human marker survives, later markers dropped

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Sync-drawer label for the new op

**Files:**
- Modify: `backend/app/templates/sync_drawer.html` (`OP_LABEL` dict, ~line 13-14)

- [ ] **Step 1: Add the label**

Change the `OP_LABEL` dict so it reads:

```jinja
{%- set OP_LABEL = {
  "AddMarkers": "Markers", "SetField": "Field", "AppendNote": "Note",
  "ReplaceNote": "Note", "ReconcileMarkers": "Switch version"} -%}
```

- [ ] **Step 2: Verify the template still parses**

Run: `.venv/bin/python -c "from backend.app.routes.pages import templates; templates.get_template('sync_drawer.html'); print('ok')"`
Expected: prints `ok` (no `TemplateSyntaxError`).

- [ ] **Step 3: Commit**

```bash
git add backend/app/templates/sync_drawer.html
git commit -m "feat(versions): sync-drawer labels ReconcileMarkers as 'Switch version'

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Rename the two "History" controls

**Files:**
- Modify: `backend/app/templates/pages/_clip_history_menu.html` (line 16)
- Modify: `backend/app/templates/pages/_anno_panels.html` (line 42)

- [ ] **Step 1: Rename the version dropdown trigger**

In `_clip_history_menu.html`, change line 16 from:

```jinja
  {% call menu(label='History', variant='ghost', size='sm', align='right') %}
```

to:

```jinja
  {% call menu(label='Versions', variant='ghost', size='sm', align='right') %}
```

- [ ] **Step 2: Rename the live-session tab**

In `_anno_panels.html`, change the History tab label (line 42) from:

```jinja
    History
```

to:

```jinja
    Live sessions
```

(Leave the `@click="tab = 'history'; if (!historyLoaded) loadHistory()"` handler and the `tab === 'history'` panel unchanged — only the visible label changes.)

- [ ] **Step 3: Verify both templates still parse**

Run: `.venv/bin/python -c "from backend.app.routes.pages import templates; templates.get_template('pages/_clip_history_menu.html'); templates.get_template('pages/_anno_panels.html'); print('ok')"`
Expected: prints `ok`.

- [ ] **Step 4: Commit**

```bash
git add backend/app/templates/pages/_clip_history_menu.html backend/app/templates/pages/_anno_panels.html
git commit -m "fix(versions): disambiguate History controls — 'Versions' dropdown vs 'Live sessions' tab

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: Stop the header row from clipping the version controls

**Files:**
- Modify: `backend/app/static/app.css` (`.anno-scope-row`, ~line 726)

- [ ] **Step 1: Allow the row to wrap**

Change the rule from:

```css
.anno-scope-row { display: flex; align-items: center; gap: 8px; margin: 8px 14px; }
```

to:

```css
.anno-scope-row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; row-gap: 8px; margin: 8px 14px; }
```

- [ ] **Step 2: Manual verification (both scopes)**

Start the dev server with the `server-start` skill. Open a clip that has an unpublished draft (so the wide `DRAFT – UNPUBLISHED` pill shows) at `/clips/{id}`. Narrow the browser window until the header is tight.
Expected: the **Versions** control wraps to a second line and stays fully visible/clickable; the popover still overlays without clipping. Toggle Published ↔ Draft — holds in both. Stop the server with the `server-stop` skill (SIGTERM; confirm seat release).

- [ ] **Step 3: Commit**

```bash
git add backend/app/static/app.css
git commit -m "fix(versions): wrap .anno-scope-row so the version controls never clip

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 8: Full suite, live CatDV smoke, ADR

**Files:**
- Create: `docs/adr/0101-faithful-version-switching-reconcile-markers.md`
- Modify: `docs/decisions.md`

- [ ] **Step 1: Run the full test suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS, no regressions. If `lint-imports` / ruff are part of CI, also run: `.venv/bin/python -m ruff check backend tests` and `.venv/bin/python -m pytest tests/unit/test_design_language_guard.py -q`.

- [ ] **Step 2: Live CatDV smoke test (gated on a free seat)**

Pre-flight per `CLAUDE.md`: confirm nothing else holds the seat
(`/usr/sbin/lsof -nP -iTCP@192.168.1.41:8080`). Start the server with the
`server-start` skill. On a **throwaway** clip:
1. Publish v1 (a couple of markers), then add a marker and publish v2, then v3.
2. (Optional) add one marker directly in the CatDV web client.
3. In the app, open **Versions** → **Make live** on v1; wait for the sync chip to go syncing → synced.
4. In CatDV, confirm the clip's marker set == v1's markers ∪ the human marker, each once, at correct timecodes; the headline reads "Live v1".

Stop the server with the `server-stop` skill (SIGTERM) and confirm the
"Application shutdown complete" seat-release line.

- [ ] **Step 3: Write the ADR**

Create `docs/adr/0101-faithful-version-switching-reconcile-markers.md` (MADR-lite: `# 0101. …`, `**Date:** 2026-06-17`, `**Status:** Accepted`, `## Context` / `## Alternatives` / `## Decision` / `## Consequences`). Capture: the additive-vs-reconcile root cause; the decision to roll back only our markers (preserve foreign/human); the fps=0.0 sentinel so frames derive at the clip's real fps; the `ReconcileMarkers` op; and the History/Versions/Live-sessions UI disambiguation. Note that already-corrupted clips from the old fps bug are not auto-repaired (out of scope). If a parallel branch already took `0101`, use the next free number.

- [ ] **Step 4: Update the decisions index**

Add a row to the table in `docs/decisions.md` for ADR 0101.

- [ ] **Step 5: Commit**

```bash
git add docs/adr/0101-faithful-version-switching-reconcile-markers.md docs/decisions.md
git commit -m "docs(adr): 0101 faithful version switching via ReconcileMarkers + History UI disambiguation

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review notes (for the implementer)

- **Spec coverage:** B1 → Tasks 2-4; B2 → Tasks 2-3 (fps=0.0 sentinel + frame at clip fps); B3a → Task 7; B3b → Task 6; decision-3 verification → Task 4 (offline dry-run) + Task 8 (live smoke). Notes/fields overwrite-only: preserved by `_switch_ops` (Task 3) emitting `SetField`/`ReplaceNote`-when-present only.
- **No new component:** the dropdown still uses `ui.menu`; the tab still uses `loadHistory()`; the CSS is a one-line change to the existing row (design-language guard safe).
- **Type consistency:** `ReconcileMarkers(desired: tuple[Marker, ...], drop_secs: tuple[float, ...])` is used identically in `model.py`, `change_set_json.py`, `payload.py`, `publish_service.py`, and all tests. The helper is named `_switch_ops` everywhere.
- **Regression guard:** existing `test_add_markers_*` stay green because the `AddMarkers` path is logically unchanged (refactored into `working` with identical output).
```
