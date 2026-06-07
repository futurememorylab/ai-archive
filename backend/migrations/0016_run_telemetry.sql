-- 0016: run telemetry + app_meta. One run_telemetry row per Gemini call,
-- written by both annotator finalize paths. Doubles as the (dormant)
-- Phase-2 outbox via sent_at/send_attempts. Rows are kept forever —
-- they are the estimator's history (~1 KB/run).
-- Idempotency key for the Phase-2 flusher is (install_id, id); no
-- separate event_id column is needed.
-- See docs/specs/2026-06-07-run-telemetry-cost-estimation-design.md.

CREATE TABLE app_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE run_telemetry (
  id                    INTEGER PRIMARY KEY,
  occurred_at           TEXT NOT NULL,
  install_id            TEXT NOT NULL,
  app_version           TEXT,
  kind                  TEXT NOT NULL CHECK (kind IN ('studio','annotation')),

  archive_id            TEXT,
  user_ref              TEXT,
  job_id                INTEGER,
  clip_id               INTEGER,
  clip_name             TEXT,

  prompt_version_id     INTEGER,
  prompt_hash           TEXT,
  schema_hash           TEXT,
  prompt_chars_rendered INTEGER,
  model                 TEXT NOT NULL,

  media_kind            TEXT,
  media_duration_secs   REAL,
  media_width           INTEGER,
  media_height          INTEGER,
  media_fps             REAL,
  media_bytes           INTEGER,
  media_ext             TEXT,
  media_resolution_setting TEXT,
  preprocess            TEXT,

  vertex_project        TEXT,
  vertex_location       TEXT,
  ai_store_kind         TEXT,

  status                TEXT NOT NULL CHECK (status IN ('ok','error')),
  error_class           TEXT,
  finish_reason         TEXT,
  attempt_count         INTEGER,
  duration_s            REAL,
  tokens_in             INTEGER,
  tokens_in_text        INTEGER,
  tokens_in_video       INTEGER,
  tokens_in_audio       INTEGER,
  tokens_in_image       INTEGER,
  tokens_cached         INTEGER,
  tokens_out            INTEGER,
  tokens_thinking       INTEGER,
  cost_usd              REAL,
  pricing_version       TEXT,

  est_tokens_in         INTEGER,
  est_tokens_out_p50    INTEGER,
  est_tokens_out_p90    INTEGER,
  est_cost_usd_p50      REAL,
  est_cost_usd_p90      REAL,
  est_confidence        TEXT,

  output_chars          INTEGER,
  review_item_count     INTEGER,

  attrs                 TEXT,
  sent_at               TEXT,
  send_attempts         INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_run_telemetry_estimator
  ON run_telemetry (model, media_kind, status, prompt_hash);
CREATE INDEX idx_run_telemetry_unsent
  ON run_telemetry (sent_at) WHERE sent_at IS NULL;
