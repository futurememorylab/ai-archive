# 0115. Resolution-aware estimates: key the learned history, keep seeds resolution-blind

**Date:** 2026-06-23
**Status:** Accepted

## Context

PR3 of the cost-prediction work makes the pre-run estimator resolution-aware:
the same clip costs different token amounts at `low`/`medium`/`high` media
resolution, so estimates should reflect the resolution a run will actually use.
The estimator (`run_estimator.py`) has two sources per media kind:

- **Learned rates** from `run_telemetry` history (input tokens/sec, output
  tokens/sec p50/p90) — the accurate, self-calibrating part.
- **Seed constants** (`SEED_INPUT_TOKENS_PER_SEC`, `SEED_OUTPUT_TOKENS_PER_SEC`,
  `IMAGE_TILE_TOKENS`) — the cold-start fallback used only when fewer than 3
  history samples exist for a key.

The spec (§3) originally said to key BOTH "seed constants and learned rates" on
`(model, kind, resolution)`, with the chain bottoming out in "resolution-scaled
seed constants." Implementing genuinely resolution-scaled seeds means encoding,
in code, how many tokens a second of video / a tile of image costs at each
resolution — and those numbers differ by model generation (Gemini 2.5 vs 3, per
the media-resolution docs: e.g. 64/256/256 tokens for low/med/high, but video
frame caps differ again). That is a fragile, fast-staling table that only ever
affects the zero-history case (where confidence is already reported as `rough`).

## Alternatives

1. **Resolution-key both history and seeds** (literal spec §3). Most "complete,"
   but requires a hardcoded per-model-per-resolution token table that drifts as
   Google changes models, for marginal benefit (cold-start only).
2. **Crude multipliers on the seeds** (e.g. low ×0.25, high ×1.0 off the medium
   baseline). Cheap, directionally resolution-aware at cold start, but the
   multipliers are approximate and still model-generation-dependent — a
   half-accurate number that looks more authoritative than it is.
3. **Resolution-key the learned history only; leave seeds resolution-blind**
   (chosen). The accuracy mechanism (history) is resolution-specific; the
   cold-start fallback is honest about being a rough, resolution-blind guess.

## Decision

Adopt alternative 3. `recent_input_ratios` / `recent_output_rates` gained an
optional `media_resolution` filter; `estimate_clips` threads the resolved
resolution into all three reads; the fallback chain is
`(prompt_hash, model, kind, resolution) → (model, kind, resolution) → seeds`,
where the seeds are the existing resolution-blind constants. The effective
resolution is resolved once per estimate in `estimate_for_clip_ids`
(`resolve_media_resolution(version.media_resolution, model_default)`) — the same
policy a real run uses — and returned to the UI.

Consequence for cold start: with zero history at a given resolution, the
estimate is identical across resolutions (and labelled `rough`). After ≥3 runs
at that resolution, the resolution-specific learned rates take over and the
estimate becomes resolution-accurate. The calibration sweep (PR4) is the
deliberate way to populate those samples for resolutions a prompt would not
otherwise exercise.

## Consequences

- **Positive:** no fragile per-model-per-resolution constant table to maintain;
  the estimator self-corrects per resolution from real data; query count is
  unchanged per kind (the resolution filter is an added WHERE clause, not an
  extra round) — the only new query is one constant `model_config` default-
  resolution read per estimate, so the N+1 guard holds (N=10 == N=100, count
  5 → 6).
- **Negative / accepted:** a brand-new model/resolution with no history gives a
  resolution-blind cold-start estimate. This is acceptable because that estimate
  is already flagged `rough`, and the gap closes after a few runs (or a
  calibration sweep). Revisit if a need arises for accurate *first-run*
  per-resolution estimates before any history exists.
- Softens spec §3's "resolution-scaled seed constants"; §3 and the
  implementation-order section were amended to match.
