# 0119. Downgrade HIGH media resolution for non-image media on every run; surface missing pricing instead of $0

**Date:** 2026-06-23
**Status:** Accepted

## Context

Two reliability gaps surfaced once calibration started exercising the
`media_resolution` setting against real clips and real (sometimes
un-priced) models:

1. **HIGH is image-only, but only calibration guarded it.** Vertex returns
   `400 INVALID_ARGUMENT "the model supports HIGH media resolution only for
   single images"` for video/audio. ADR 0116 added `resolution_valid_for_kind`
   and applied it in the calibration sweep, but the *normal* run path
   (`annotator._process_item`) did not: when a model's
   `default_media_resolution` (or a prompt-version override) resolved to
   `high` and the clip was a video, every production annotation/studio run
   400-ed. The model-default is admin-set and applies to all clips, so a
   single "high" default silently broke video annotation.

2. **A model with no rate card recorded $0, not "unknown".** `compute_cost`
   correctly returns `None` when a model has no `model_config` row, and
   `run_telemetry.cost_usd` stored `NULL` — but `_finalize_studio` wrote
   `cost_usd or 0.0` (a real run looked *free*), the calibrate results panel
   summed `NULL→0` and rendered `$0.00 · fair` (looked calibrated and free),
   the projected-cost line went silently blank, and the batches/studio
   estimate showed `~— (good)` (a dollar em-dash next to a confidence).

## Alternatives

- **Block/҂error HIGH-on-video** (refuse the run) — rejected: punishes the
  user for an admin default they may not control; medium is a safe, cheaper,
  always-valid fallback that still produces a result.
- **Validate at prompt-save / model-config-save time only** — necessary as
  defense-in-depth but insufficient: the model default applies to clips of
  every kind, so the kind isn't known until run time. The load-bearing guard
  must be at the point of the call.
- **Leave cost as $0 when unpriced** — rejected: indistinguishable from a
  genuinely free run; corrupts budget/audit math and hides that pricing needs
  setting.

## Decision

1. **General downgrade guard.** In `_process_item`, resolve `media_resolution`
   (force_resolution → version override → model default → 'medium') *before*
   the pre-call estimate, then unconditionally: `if not
   resolution_valid_for_kind(media_resolution, media_kind): media_resolution =
   "medium"`. This covers both the calibration `force_resolution` branch and
   the normal branch — `high` can never reach `gemini.annotate` for
   non-image media. The downgrade is logged (`log.info`) so an admin can
   discover their `high` was overridden; the effective value is what's stored
   in `run_telemetry.media_resolution_setting`, so audits reflect what ran.

2. **Resolution-aware in-run estimate.** Because the resolution is now
   resolved before the estimate, the in-run estimate stamped onto each
   telemetry row (`est_*`) is computed at the actual resolution — the
   est-vs-actual delta and the per-resolution feedback loop are no longer
   biased by a resolution-blind estimate.

3. **Missing pricing is surfaced, never $0.** `_finalize_studio` stores the
   real `cost_usd` (NULL when unknown). `estimate_for_clip_ids` returns
   `pricing_missing = (cost_usd_p50 is None)` (tokens are still computed).
   Every cost surface branches on it: the calibrate results panel shows a
   `no rate card` pill + a "set pricing" link to the Gemini-models tab; the
   projected-cost line says "No rate card for this model — cost not
   projected"; the batches/studio estimate says "cost unknown — no rate card"
   instead of a dollar range. Runs of un-priced models still complete.

## Consequences

- **Positive:** no production run can 400 on HIGH-vs-video; un-priced models
  run and are clearly flagged rather than silently logged as free; estimates
  are resolution-consistent. `resolution_valid_for_kind` stays the single
  source of truth (reused, not re-implemented).
- **Negative / accepted:** the downgrade silently overrides an admin's `high`
  for video (mitigated by the log line + the models-page help copy explaining
  HIGH is image-only). The projected calibration cost remains an approximation
  (priced at the prompt's effective resolution, scaled by run count, not
  per-swept-resolution) — acceptable for an advisory confirm; a per-resolution
  projection is a possible future refinement.
- **UI parity fix (same session):** the calibrate clip picker was wrapping the
  shared picker in `.modal-body nb-body`, whose `flex-direction:column`
  overrode the side-by-side `.nb-body` layout (basket stacked below the list).
  Extracted the canonical picker body into `_clip_picker_modal_body.html`,
  reused by both the calibrate dialog and the studio archive picker, so the
  picker is identical to the Batches one. The Gemini-models admin page also got
  polish: native number-spinner arrows removed, the previously-unstyled
  `.pill.warn` defined, rate inputs aligned, and a media-resolution explanation
  (low/medium/high, HIGH image-only) added where the value is chosen.
