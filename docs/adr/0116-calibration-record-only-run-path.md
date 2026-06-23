# 0116. Calibration reuses the run path with a `record_only` flag (no new run-kind)

**Date:** 2026-06-23
**Status:** Accepted

## Context

PR4a adds a calibration sweep: an admin picks 3 clips for a prompt version and
runs them at each media resolution, twice (3 × {low, medium, high} × 2 = 18
real Gemini runs), so the estimator gains per-resolution history for resolutions
a prompt would otherwise never exercise. The **only** thing calibration needs to
produce is `run_telemetry` rows (token usage + cost at each resolution). It must
NOT pollute real data — no applied annotations, no studio-runs, no review-items
appearing on the chosen clips.

The existing annotator (`_process_item`) does a shared pipeline — resolve clip →
AI store → `gemini.annotate` → finalize — where the `kind` decides the finalize
step: an **annotation** job writes annotations onto the clip; a **studio** job
writes a `studio_run` + `review_items`. Both record one telemetry row inside
their finalize. Two questions arose: how to make calibration write telemetry
only, and how to force a specific resolution per run (the resolver normally picks
override → model default → 'medium').

## Alternatives

1. **New `calibration` run-kind.** A third `kind` with its own finalize. But
   `run_telemetry.kind` has a `CHECK (kind IN ('studio','annotation'))`, and
   `jobs`/UI branch on kind — a new value means a migration to relax the CHECK
   plus touching every kind-switch. Heavy for a behaviour that is "studio, but
   don't write the outputs."
2. **Temporary prompt-version clones**, one per resolution, with the override
   set, run as normal jobs. Pollutes the version list, mutates data, and still
   writes annotations/studio-runs.
3. **A `record_only` flag on the existing run path** (chosen): calibration runs
   are ordinary `kind="studio"` jobs launched with `record_only=True` and a
   `force_resolution`. After the Gemini call, `_process_item` records the
   telemetry row and marks the item done, then returns — skipping BOTH
   `_finalize_studio` and `_finalize_annotation`. A sibling `force_resolution`
   flag bypasses the resolver so the sweep can drive each resolution.

## Decision

Adopt alternative 3. `run_job` and `_process_item` gain two keyword-only flags,
threaded through `start_job_in_background`:

- `force_resolution: str | None` — when set, `media_resolution = force_resolution`
  (the resolver is skipped); otherwise the normal override → model-default →
  'medium' resolution runs.
- `record_only: bool` — when True, the finalize branch becomes a telemetry-only
  path: `update_item_status(..., "review_ready")` + one
  `_record_telemetry(kind="studio", status="ok", media_resolution_setting=...)`,
  and the `_finalize_studio`/`_finalize_annotation` calls are unreachable.

Calibration creates 6 `kind="studio"` jobs (3 resolutions × 2 repeats), each with
the 3 clips, tagged with a shared `run_group="calibration:<version>:<ts>"`, each
launched `record_only=True, force_resolution=<res>`. The telemetry rows carry
`kind="studio"` (they are studio-style records) and the forced
`media_resolution_setting`. The results panel reads `run_telemetry` grouped by
`media_resolution_setting` per prompt version.

Both flags default to None/False, so every existing caller (normal annotation and
studio runs, the studio route) is byte-for-byte unchanged — the `record_only`
branch sits above the untouched `elif kind == 'studio' / else` finalize arms.

## Consequences

- **Positive:** no migration, no new kind, no data pollution — calibration writes
  telemetry only; the success path skips both finalize functions (asserted by
  `test_record_only_writes_telemetry_but_no_studio_or_review`). `force_resolution`
  is a clean, reusable override (also handy for future "run this at resolution X"
  needs). The whole sweep rides the existing background-job machinery
  (`start_job_in_background`).
- **Negative / accepted:** `record_only` hardcodes the telemetry `kind="studio"`,
  so passing it on a non-studio job would mislabel the row — acceptable because
  the only caller (calibration) always creates studio jobs. The error path's
  `complete_error(studio_run...)` in `run_job` can still fire if a `record_only`
  studio job throws AND a `studio_run` exists — but calibration's `create_job`
  never makes a `studio_run`, so `find_latest_id_for_job_clip` returns None and
  the branch is skipped in practice.
- **Real spend:** the sweep makes 18 real Gemini calls per invocation, behind an
  admin action + a projected-cost confirm. Tests use a fake Gemini (the launch is
  monkeypatched in `test_admin_calibrate`).
