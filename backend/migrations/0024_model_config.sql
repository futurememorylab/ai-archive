-- 0024: per-model pricing + default media resolution. Replaces the hardcoded
-- RATE_CARDS dict in services/pricing.py. A row is materialised either by
-- boot-time reconcile (the SEED_RATE_CARDS seed) or by an admin edit. `removed`
-- is a soft delete so reconcile won't re-add a model the admin deleted. Editing
-- bumps pricing_version (snapshot-at-write: past run_telemetry.cost_usd is never
-- rewritten). default_media_resolution is reserved for PR2 (resolution wiring).
-- See docs/specs/2026-06-22-accurate-resolution-aware-cost-prediction-design.md.
CREATE TABLE model_config (
  model                          TEXT    NOT NULL PRIMARY KEY,
  input_text_video_image_per_1m  REAL    NOT NULL,
  input_audio_per_1m             REAL    NOT NULL,
  input_cached_per_1m            REAL    NOT NULL,
  output_per_1m                  REAL    NOT NULL,
  source_url                     TEXT    NOT NULL DEFAULT '',
  default_media_resolution       TEXT    NOT NULL DEFAULT 'medium',
  pricing_version                TEXT    NOT NULL,
  updated_at                     TEXT    NOT NULL,
  removed                        INTEGER NOT NULL DEFAULT 0,
  created_at                     TEXT    NOT NULL DEFAULT (datetime('now'))
);
