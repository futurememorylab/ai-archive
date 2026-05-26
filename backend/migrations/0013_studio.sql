-- 0013: Prompt Studio tables. studio_folder/studio_folder_clip hold the
-- iteration workspace (clips picked from the archive, organized into
-- flat folders). studio_run stores one row per studio execution
-- (kept forever; UI shows the latest per version+clip).
-- jobs.kind discriminates the worker path: NULL=annotation (writes to
-- CatDV), 'studio'=studio run (writes only to studio_run, skips CatDV).

CREATE TABLE studio_folder (
  id         INTEGER PRIMARY KEY,
  name       TEXT    NOT NULL UNIQUE,
  created_at TEXT    NOT NULL
);

CREATE TABLE studio_folder_clip (
  folder_id INTEGER NOT NULL REFERENCES studio_folder(id) ON DELETE CASCADE,
  clip_id   INTEGER NOT NULL,
  added_at  TEXT    NOT NULL,
  PRIMARY KEY (folder_id, clip_id)
);

CREATE TABLE studio_run (
  id                INTEGER PRIMARY KEY,
  prompt_version_id INTEGER NOT NULL REFERENCES prompt_versions(id),
  clip_id           INTEGER NOT NULL,
  job_id            INTEGER REFERENCES jobs(id),
  status            TEXT    NOT NULL CHECK (status IN ('pending','running','ok','error')),
  output_json       TEXT,
  duration_s        REAL,
  tokens_in         INTEGER,
  tokens_out        INTEGER,
  cost_usd          REAL,
  model             TEXT,
  error             TEXT,
  started_at        TEXT,
  finished_at       TEXT
);

CREATE INDEX studio_run_lookup
  ON studio_run(prompt_version_id, clip_id, finished_at DESC);

CREATE INDEX studio_run_by_clip
  ON studio_run(clip_id, status, prompt_version_id);

ALTER TABLE jobs ADD COLUMN kind TEXT;
