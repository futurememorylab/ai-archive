# Plan — Writeback redesign: AI-owned markers and description (#123)

Spec: `docs/specs/2026-10-14-writeback-redesign-ai-markers-design.md`.
Work TDD: write the failing test first, then the code. Make one commit per
task.

### Task 1: Live CatDV probe (needs human OK — uses the seat)

- Write `tools/probe_catdv_writeback.py`. It logs in with `POST /session`,
  then works on one scratch clip:
  - GET the clip with `?include=markers,clip.fields`;
  - PUT one marker with `category:"AI"`, then GET;
  - PUT `fields:{"pragafilm.popis.ai":"x"}` only, then GET and check the other
    fields are intact;
  - PUT `""` to that field, then GET;
  - restore the original markers and fields;
  - **always** `DELETE /session` in a `finally` block.
- Record the results in the spec's facts table, updating the unverified rows.
- Proves: the facts table has no unverified rows.

### Task 2: `ReplaceAIMarkers` op + JSON round-trip

- Files: `archive/model.py`, `archive/change_set_json.py`,
  `templates/sync_drawer.html` (label).
- Test: `tests/unit/test_change_set_json.py`. It round-trips
  `ReplaceAIMarkers`, and old `AddMarkers`/`ReconcileMarkers` JSON still
  deserializes.

### Task 3: CatDV payload rule for AI markers

- Files: `archive/providers/catdv/payload.py`, `mapping.py`. Add the category
  name prefix and `category="AI"`, and stop sending `color`.
- Test: `tests/unit/test_catdv_payload.py`. It checks that:
  - non-AI markers are kept and AI markers are replaced;
  - a legacy marker is dropped only on a frame + name match;
  - a human marker on the same frame with a different name is kept;
  - the prefix stays within `MARKER_NAME_MAX`.

### Task 4: Publish emits `ReplaceAIMarkers`

- File: `services/write_queue.py::_items_to_change_ops`.
  - `desired` = all accepted marker items of the draft;
  - nothing is emitted when the draft has no marker items;
  - `legacy` comes from the clip's snapshots.
- Test: `tests/integration/test_write_queue.py`.

### Task 5: Snapshots keep real note targets; Make live uses the new op

- Files: `services/publish_service.py` (`_materialize`, `_switch_ops`),
  `services/clip_versions_backfill.py`, `services/restore_service.py`.
- In `_materialize`, the markers become the draft's accepted set when the
  draft has markers.
- Test: `tests/unit/test_version_switch_fidelity.py`. *Make live* emits
  `ReplaceNote("pragafilm.popis.ai")`, not `notes`.

### Task 6: Summary goes to `pragafilm.popis.ai` (replace)

- Files: `backend/seeds/default_template.json`, `image_template.json`.
- Test: new `tests/unit/test_seed_target_map.py`. The seeded `target_map` routes
  `summary_cz` to `pragafilm.popis.ai` with mode `replace`.

### Task 7: FS sidecar adapter supports `ReplaceAIMarkers`

- File: `archive/providers/fs/adapter.py`.
- Test: `tests/integration/test_fs_adapter.py` plus
  `tests/contract/test_archive_provider.py`, with the same rule as CatDV.

### Task 8: Fake CatDV models wholesale marker replace

- File: `tests/fakes/fake_catdv.py`. A PUT body's `markers` replaces the list,
  and `fields` merge per key.
- Test: the existing integration suite still passes with this fake.

### Task 9: Walkthrough — publish turns bands orange; update has no duplicates

- File: `tests/walkthrough/scenarios/review_edit_annotation.py` (extend it).
  The walkthrough fake applies ops through the payload rule.
- Test: `/e2e` in assert mode.

### Task 10: ADR + invariants

- Write `docs/adr/0127-ai-category-owns-writeback.md` and index it in
  `docs/decisions.md`.
- Update Invariant 9/9a in `docs/architecture-invariants.md`.
- Test: none (docs).
