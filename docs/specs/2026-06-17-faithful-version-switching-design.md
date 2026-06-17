# Faithful version switching + History UI fixes

**Date:** 2026-06-17
**Status:** Design — approved for planning
**Author:** brainstorming session (adam + Claude)

Supersedes the marker half of the switching behaviour shipped in
[ADR 0100](../adr/0100-publishing-audit-drop-provenance-and-reactivate-switching.md)
(A3 re-based switching onto `PublishService.reactivate`, but the re-PUT was
expressed with additive ops and a hardcoded fps).

## Problem

Operator QA after ADR 0100 found that **"Make live" on an older version does
not actually switch the clip**, plus a header UI defect on the clip-detail
page. Three concrete failures, all reproduced against live code
(`/tmp/repro_switch.py` drives the real `_ops_from_snapshot` →
`build_put_payload`).

### B1 — Switching leaves later-version markers on the clip

`reactivate` re-asserts a version's snapshot with `AddMarkers`, which is
**additive**. `build_put_payload` (`payload.py:68`) deliberately preserves
existing markers it didn't touch (correct for publish-forward, wrong for a
switch). So switching back never removes what later versions added.

| Setup | Switch to v1 (snapshot = A,B) | Got | Want |
|---|---|---|---|
| clip has A,B,C,D @ 25fps | re-PUT | `A,B,C,D` | `A,B` |

The headline reads "Live v1" but the clip is still the v3 superset.

### B2 — Switching duplicates / misplaces markers on non-25fps clips

`reactivate` bakes `DEFAULT_FPS = 25.0` into every marker
(`publish_service.py:143,157`), and `_timecode_to_catdv` derives the frame as
`round(secs * tc.fps)` from that baked fps (`mapping.py:124-129`). The dedup
key is the integer frame `in.frm`. On any clip whose real fps ≠ 25 the
recomputed frame never matches the existing marker's frame, so dedup misses and
the marker is duplicated at a shifted frame.

| Setup | Switch to v1 (A@4s,B@8s) | Got | Want |
|---|---|---|---|
| clip @ 30fps has A@frm120, B@frm240 | re-PUT | `A@100, A@120, B@200, B@240` | `A@120, B@240` |

### B3 — Header UI: a clipped dropdown and two controls both called "History"

On the clip-detail annotation column (see screenshot, 2026-06-17):

- **B3a — the version-History dropdown is clipped at the right edge.**
  `.anno-scope-row` (`app.css:726`) is a **non-wrapping** flex row
  (`display:flex`, no `flex-wrap`). Its contents — the Published/Draft tabs, the
  wide `DRAFT – UNPUBLISHED` status pill, and the History dropdown — exceed the
  column width, so `#version-panel` is pushed past the right edge and cut off.
  (Commit 91c4dc9 fixed the *popover* clipping; the *trigger button* still
  overflows.)
- **B3b — two controls are both labelled "History"** on the same screen,
  meaning different things:
  - the top dropdown (`_clip_history_menu.html`, in `#version-panel`) =
    **version / publish history** (Make live · Edit as draft);
  - the panel sub-tab (`_anno_panels.html`, loads `GET /clips/{id}/live-history`
    → `_anno_live_history.html`) = **live AI-session history**.

## Decisions (this session)

1. **Roll back only *our* markers.** Our snapshots only ever record markers the
   app authored (`_load_live_snapshot` starts from an empty base;
   `clip_versions_backfill` builds from our synced review_items). Therefore a
   switch must preserve any pre-existing or human-added CatDV marker and only
   remove/re-add markers we own. A naïve wholesale replace is **rejected** — it
   would silently delete an editor's manual markers.
2. **Markers + notes faithful now; fields overwrite.** Markers become exact
   (decision 1). Notes/bigNotes overwrite to the version's value *when the
   version has them* and are **not** cleared when absent (so a human note is
   never destroyed). Fields `SetField`-overwrite; a field a *later* version
   newly introduced may linger. Full field/note clearing is **out of scope**
   (needs CatDV field-clear semantics we can't safely verify before the demo).
3. **Verification:** comprehensive unit + integration tests, an offline payload
   dry-run asserting the exact PUT bytes, **and** one live CatDV smoke test on a
   throwaway clip (graceful shutdown / single-seat discipline per `CLAUDE.md`).
4. **UI:** let the header row wrap so the version controls are never clipped;
   disambiguate the two "History" labels — **recommended:** rename the version
   dropdown to **"Versions"** and the live-session tab to **"Live sessions"**.
   (Wording is the one open point for spec review.)

## Design

### New writeback op: `ReconcileMarkers`

The fix lives where the live clip and its real fps are known — drain time, in
`build_put_payload`. A new op carries exactly what a faithful switch needs:

```python
@dataclass(frozen=True)
class ReconcileMarkers:
    desired: tuple[Marker, ...]   # the target version's markers (ours, to assert)
    drop_secs: tuple[float, ...]  # in-points of OUR markers in other versions, to remove
```

`build_put_payload` computes the final marker array (CatDV replaces the markers
array wholesale on PUT, so the payload *is* the final set), keyed entirely on
the **clip's real fps**:

```python
fps         = _clip_fps(current)                       # real fps, read from the live clip
desired     = [marker_to_catdv(m, fps) for m in op.desired]
desired_frm = {_in_frm(m) for m in desired}
drop_frm    = {round(s * fps) for s in op.drop_secs}
kept = [m for m in existing                            # everything we don't own…
        if _in_frm(m) not in drop_frm                 #   …minus our other-version markers
        and _in_frm(m) not in desired_frm]            #   …minus ones we're re-asserting
payload["markers"] = kept + desired                   # foreign/human markers survive
```

Guarantees: foreign/human markers preserved (decision 1); our later-version
markers removed (B1 fixed); target markers overwritten with our correct copies
(anti-mojibake from 414cb9b still holds); every frame derived at real fps (B2
fixed — no duplication, no shift).

**Op vocabulary surface** (all small, mechanical):
- `model.py` — the dataclass + add to the `ChangeOp` union.
- `change_set_json.py` — `change_op_to_dict` / `change_op_from_dict` round-trip
  (so it survives `pending_operations.op_json`).
- `payload.py` — the branch above.
- `templates/sync_drawer.html` — an `OP_LABEL` entry (e.g. "Switch version").

**Concurrency:** if an `AddMarkers` (publish) and a `ReconcileMarkers` (switch)
ever land in one drain, `build_put_payload` applies `AddMarkers` merges first,
then `ReconcileMarkers` on top — deterministic, reconcile wins. In practice a
switch clears the working draft, so they don't co-occur.

### The fps fix (B2)

`reactivate` builds each `desired` marker with the fps **sentinel `0.0`**.
`_timecode_to_catdv`'s existing fallback (`fps = tc.fps if tc.fps > 0 else
default_fps`) then re-derives the frame from the clip's real fps passed by
`build_put_payload`. This reuses logic already in the codebase rather than
threading a new fps parameter or reading the clip at enqueue time (which would
spend a CatDV seat and break offline).

### `reactivate` changes (`publish_service.py`)

`reactivate` already loads `list_by_clip`. It now:

1. `ours_all_secs = ∪` over **all** the clip's versions of their snapshot
   markers' in-seconds.
2. `target_secs = {m.in.secs for m in target.snapshot.markers}`.
3. `drop_secs = ours_all_secs − target_secs`.
4. Emits **always** (even when the target has zero markers — that's how
   switching to an empty version still strips our later additions):
   `ReconcileMarkers(desired=<target markers @ fps 0.0>, drop_secs=drop_secs)`,
   plus the existing `SetField` (fields) and `ReplaceNote`-when-present (notes).
5. The empty-ops guard stays: if there is genuinely nothing to assert and
   nothing to drop and no fields/notes, `mark_live` directly (no PUT).

`_ops_from_snapshot`'s signature changes to take the full version list (or the
precomputed `drop_secs`) since "what to drop" is cross-version, not derivable
from a single snapshot. It is only called by `reactivate`, so the change is
contained.

The SyncEngine's `origin_clip_version_id` plumbing and `mark_live`-on-success
are unchanged — the version still flips live exactly as today once the PUT
lands.

### UI fixes (B3)

- **B3a clipping:** add `flex-wrap: wrap` (+ a `row-gap`) to `.anno-scope-row`
  so, when the column is narrow, `#version-panel` wraps to a second line and the
  version controls are always fully visible. No new component — a one-line CSS
  change to the existing row. Verify in **both** the draft and published scopes
  (per `CLAUDE.md`'s Alpine/CSS guidance).
- **B3b labels:** rename the dropdown trigger "History" → **"Versions"**
  (`_clip_history_menu.html`, `menu(label=…)`) and the panel tab "History" →
  **"Live sessions"** (`_anno_panels.html`). Pure label changes; the underlying
  routes/partials are untouched. The dropdown keeps using `ui.menu` and the tab
  keeps its `loadHistory()` lazy fetch.

## Testing

1. **Unit — `tests/unit/test_catdv_payload.py`:** `ReconcileMarkers` drops our
   markers, preserves a foreign marker at an untouched frame, overwrites at a
   shared frame (ours wins), and re-derives frames at 30fps. The two repro cases
   (B1, B2) become passing assertions.
2. **Round-trip — `tests/unit/test_change_set_json.py`:** `ReconcileMarkers`
   serialises/deserialises through `op_json` unchanged.
3. **Integration — `tests/integration/test_publish_service.py`:** switching
   v3→v1 enqueues one `ReconcileMarkers` carrying the right `desired` /
   `drop_secs`, stamped with v1's `origin_clip_version_id`, and creates **no**
   new `clip_versions` row.
4. **Offline dry-run:** build the exact PUT body for a v3→v1 switch on a
   synthetic clip that carries one "human" marker at an in-point we never
   authored; assert the human marker is present and C/D are gone.
5. **Live CatDV smoke (decision 3):** on a throwaway clip, publish v1→v2→v3,
   then "Make live" v1; confirm in CatDV the marker set == v1's markers ∪ any
   human marker, and the headline reads "Live v1". Shut the dev server down with
   `SIGTERM` and confirm seat release.
6. **Regression:** existing `test_add_markers_*` (publish-forward, additive)
   stay green — the new op does not change `AddMarkers`.

## Manual acceptance flows

1. **Faithful marker rollback (B1).** Open a clip at `/clips/{id}` with ≥3
   published versions where each added markers. Open **Versions**, click **Make
   live** on v1. Watch the sync chip go syncing→synced. Reload; the Published
   markers panel shows exactly v1's markers (later-version markers gone) and the
   headline pill reads "Live v1".
2. **No duplication on non-25fps (B2).** Repeat flow 1 on a clip whose fps ≠ 25
   (e.g. 30 or 23.976). After the switch, the markers panel shows each marker
   once, at the correct timecode — no doubled or shifted markers.
3. **Human marker survives (decision 1).** Before switching, add a marker
   directly in the CatDV web client at a time the app never used. Run flow 1.
   After the switch, that manual marker is still on the clip.
4. **Header never clips (B3a).** Open a clip with an unpublished draft (so the
   `DRAFT – UNPUBLISHED` pill shows) and narrow the window / annotation column.
   The **Versions** control stays fully visible (wraps to a second line if
   needed) and is clickable; the History popover still overlays without
   clipping. Check in both the Published and Draft scopes.
5. **No duplicate "History" (B3b).** On the same clip, confirm the top control
   reads **Versions** (lists publish snapshots with Make live / Edit as draft)
   and the panel tab reads **Live sessions** (lists live AI sessions). No two
   controls share a label.

## Out of scope

- Clearing fields a later version newly introduced (decision 2).
- Cleaning up marker duplicates left on already-corrupted clips by the *old* fps
  bug — the smoke test uses a fresh clip; a backfill/repair pass is a separate
  task if needed.
- Two-way reconciliation from CatDV (already out of scope in the parent feature
  spec).
