-- Persist per-job run parameters so the lifespan-owned JobRunner (ADR 0125)
-- can run a calibration sweep job exactly as the old route-spawn did — with a
-- forced media resolution and telemetry-only (record_only) — instead of those
-- args living only in the spawning request. Normal annotation/studio jobs leave
-- both at their defaults (no forced resolution, record_only = 0).
ALTER TABLE jobs ADD COLUMN force_resolution TEXT;
ALTER TABLE jobs ADD COLUMN record_only INTEGER NOT NULL DEFAULT 0;
