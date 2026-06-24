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

Calibration creates `kind="studio"` jobs (each resolution × 2 repeats) over **any
number of selected clips (≥1)** — the picker behaves like the Batches "New batch"
picker, not a fixed-3 rule. Each job is tagged with a shared
`run_group="calibration:<version>:<ts>"` and launched
`record_only=True, force_resolution=<res>`.

**Per-clip resolution validity (refinement).** HIGH media resolution is valid
**only for single still images** — Vertex returns `400 INVALID_ARGUMENT "the model
supports HIGH media resolution only for single images"` for video/audio. So for
each resolution we build the subset of selected clips it's valid for
(`resolution_valid_for_kind`: HIGH ⇒ image only; LOW/MEDIUM ⇒ all) and create the
2 repeat-jobs only for that subset, skipping a resolution entirely when no clip
qualifies. An all-video selection therefore yields only low+medium (4 jobs, no
high); a HIGH job NEVER contains a video/audio clip. The launch route reports the
actual job count via the `X-Calibration-Jobs` response header. The telemetry rows carry
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
- **Real spend:** the sweep makes real Gemini calls per invocation (up to 3
  resolutions × 2 repeats × N clips), behind an admin action + a projected-cost
  confirm. Tests use a fake Gemini (the launch is monkeypatched in
  `test_admin_calibrate`).
- **Per-clip eligibility refinement (2026-06-23):** the original 3-clip,
  fixed-6-job design was relaxed to any clip count (≥1) and per-clip resolution
  validity. The trigger was a live `400 INVALID_ARGUMENT "HIGH media resolution
  only for single images"` from Vertex on a video calibration run — HIGH must be
  scoped to image clips, so the sweep now skips HIGH for any non-image clip and
  drops a resolution with no eligible clip. The projected-cost estimate scales by
  the real (per-clip-valid) run count, and the launch returns the count via
  `X-Calibration-Jobs`. `resolution_valid_for_kind` (in `services/resolution.py`)
  is the single source of truth, asserted by `test_resolution_validity.py` and the
  `test_calibrate_video_clips_skip_high` / `test_calibrate_mixed_high_only_images`
  integration tests (a HIGH job never carries a video clip).
