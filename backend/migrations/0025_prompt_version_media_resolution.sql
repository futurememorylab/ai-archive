-- 0025: optional per-prompt-version media-resolution override. NULL = use the
-- model's default_media_resolution. Versioned with the prompt (clones copy it).
-- See docs/specs/2026-06-22-accurate-resolution-aware-cost-prediction-design.md §2.
ALTER TABLE prompt_versions ADD COLUMN media_resolution TEXT;
