# Plan — Writeback redesign: AI-owned markers and description (#123)

Spec: `docs/specs/2026-10-14-writeback-redesign-ai-markers-design.md`.
Work TDD: write the failing test first, then the code. Make one commit per
task. The rule throughout is that the app writes **only** `category == "AI"`
markers and `AI_OWNED_FIELDS`; nothing else is ever touched.

### Task 1: Live CatDV probe (needs a human OK, because it uses the seat)

- Write `tools/probe_catdv_writeback.py`. It runs `POST /session`, then on one
  scratch clip:
  - GET `?include=markers,clip.fields`.
  - PUT the markers with one extra `category:"AI"` marker, then GET. Check that
    the human markers are byte-identical.
  - PUT `fields:{"pragafilm.popis.ai":"x"}` only, then GET. Check that the
    other fields are intact.
  - PUT `""` to that field, then GET.
  - Restore the original state.
  - It **always** sends `DELETE /session` in a `finally`.
- Record the results in the spec's facts table.
- Proof: no row in the facts table is still marked unverified.

### Task 2: Ownership constants + `ReplaceAIMarkers` op

- Files: `archive/model.py`, which gets `AI_MARKER_CATEGORY`,
  `AI_OWNED_FIELDS` and the new op. Also `archive/change_set_json.py`, and
  `templates/sync_drawer.html` for the op's label.
- Test: `tests/unit/test_change_set_json.py`. It round-trips the new op and
  checks that old `AddMarkers`/`ReconcileMarkers` JSON still deserializes.

### Task 3: CatDV payload guard + AI marker replace

- File: `archive/providers/catdv/payload.py`:
  - Non-AI markers pass through byte-identical; AI markers are fully replaced.
  - Legacy `AddMarkers`/`ReconcileMarkers` rows are routed through the same
    rule.
  - A field or note target outside `AI_OWNED_FIELDS` raises
    `FatalProviderError`.
  - `drop_secs` is removed.
- File: `mapping.py`. Adds the `[category]` name prefix inside the clamp, sets
  `category="AI"`, and stops sending `color`.
- Tests:
  - `tests/unit/test_catdv_payload.py` covers:
    - a human marker on the same frame survives;
    - legacy markers survive;
    - a write to `popis.materialu`, decade or `notes` raises an error;
    - the prefix stays within `MARKER_NAME_MAX`.
  - New guard test `tests/unit/test_writeback_ownership_guard.py`, with a
    docstring that states the rule.

### Task 4: Write queue emits only owned ops; shared fields become suggestions

- File: `services/write_queue.py::_items_to_change_ops`:
  - `desired` is every accepted marker item of the draft.
  - When there are no marker items, no marker op is emitted.
  - Field and note items outside `AI_OWNED_FIELDS` are never enqueued.
- File: the review-items repo / draft roll-up. Suggestion items are left out
  of the pending count.
- Tests: `tests/integration/test_write_queue.py` and
  `test_review_queue_and_draft_data.py`.

### Task 5: Snapshots keep real targets; Make live is owned-only

- Files:
  - `services/publish_service.py` (`_materialize`, `_switch_ops` →
    `ReplaceAIMarkers` plus only the `AI_OWNED_FIELDS` ops);
  - `services/clip_versions_backfill.py`;
  - `services/restore_service.py`.
- Test: `tests/unit/test_version_switch_fidelity.py` checks that Make live
  never emits `notes`, `popis.materialu` or decade.

### Task 6: Seeds route the summary to `pragafilm.popis.ai` (replace)

- Files: `backend/seeds/default_template.json`, `image_template.json`.
- Test: a new `tests/unit/test_seed_target_map.py`.

### Task 7: FS sidecar adapter — same op, same guard

- File: `archive/providers/fs/adapter.py`.
- Tests: `tests/integration/test_fs_adapter.py` plus
  `tests/contract/test_archive_provider.py`. The contract test asserts that
  both providers keep non-AI markers unchanged.

### Task 8: Fake CatDV models a wholesale marker replace

- File: `tests/fakes/fake_catdv.py`. A PUT body's `markers` replaces the whole
  list, and `fields` merge per key.
- Test: the existing integration suite still passes.

### Task 9: Provenance view model

- Files:
  - `ui/view_models.py`: `_marker_view` gets `origin`, and the `[cat]` prefix
    is stripped back into `category`. `draft_review_arrays` gets
    `origin: "ai_draft"`, and suggestion items are flagged.
  - Field views get `origin`.
- Test: `tests/unit/test_draft_review_arrays.py` and
  `tests/unit/test_view_models*.py`.

### Task 10: Provenance UI — pills, three band colours, legend, suggestions

- Files:
  - `templates/pages/_anno_panels.html`: `ui.status_pill` for each
    marker/field, human cards read-only, suggestion rows with a copy button,
    and labelled summary blocks.
  - `_player_overlay.html`: the published row is split by origin, and the
    legend changes.
  - `static/app.css`: `.range.origin-human`, using existing tokens only.
  - `static/review.js`: `_refreshPublished` keeps `origin`.
- Test: `tests/unit/test_templates_shared.py` still passes. Snapshot-render
  tests in `tests/unit/test_draft_view.py`.

### Task 11: Walkthrough — provenance, publish turns orange, no duplicates

- File: `tests/walkthrough/scenarios/review_edit_annotation.py` (extend it).
  The walkthrough fake seeds one human marker and one legacy marker, and
  applies ops through the payload rule.
  - Assert the pills and band colours.
  - Assert the human marker survives a publish.
  - Assert that the bands turn orange after sync.
- Test: `/e2e` in assert mode.

### Task 12: ADR + invariants

- Write `docs/adr/0127-writeback-owns-only-ai.md`, and index it in
  `docs/decisions.md`.
- Update Invariant 9/9a in `docs/architecture-invariants.md`.
- Update `docs/design-language.md` with the provenance colours.
- Test: none (docs only).
