-- 0033: allow run_telemetry.kind = 'live' so Gemini Live audio sessions and
-- their summary call are recorded alongside annotation / studio runs (they
-- were the only Gemini spend with no row). SQLite can't ALTER a CHECK, so
-- rebuild the table with the identical column list and re-create its indexes.
-- See docs/specs/2026-10-17-human-edit-and-cost-logging-design.md.

CREATE TABLE run_telemetry_new (
  id                    INTEGER PRIMARY KEY,
  occurred_at           TEXT NOT NULL,
  install_id            TEXT NOT NULL,
  app_version           TEXT,
  kind                  TEXT NOT NULL CHECK (kind IN ('studio','annotation','live')),

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

INSERT INTO run_telemetry_new SELECT * FROM run_telemetry;
DROP TABLE run_telemetry;
ALTER TABLE run_telemetry_new RENAME TO run_telemetry;

CREATE INDEX idx_run_telemetry_estimator
  ON run_telemetry (model, media_kind, status, prompt_hash);
CREATE INDEX idx_run_telemetry_unsent
  ON run_telemetry (sent_at) WHERE sent_at IS NULL;
CREATE INDEX idx_run_telemetry_estimator_res
  ON run_telemetry (model, media_kind, media_resolution_setting, status, prompt_hash);
CREATE INDEX idx_run_telemetry_occurred_at
  ON run_telemetry (occurred_at);
