-- 0026: resolution-aware estimator index. The estimator now filters on
-- media_resolution_setting (PR3), so add it to the covering index ahead of
-- status/prompt_hash. Additive; the 0016 index stays for any resolution-blind
-- read. See docs/specs/2026-06-22-accurate-resolution-aware-cost-prediction-design.md §3.
CREATE INDEX idx_run_telemetry_estimator_res
  ON run_telemetry (model, media_kind, media_resolution_setting, status, prompt_hash);
