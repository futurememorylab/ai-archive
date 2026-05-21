-- 0009: Replace templates with prompts + prompt_versions, rewire annotations + jobs.
-- Migration is irreversible. Each existing templates row -> 1 prompt + 1 v1@production.

PRAGMA foreign_keys=OFF;

CREATE TABLE prompts (
  id            INTEGER PRIMARY KEY,
  name          TEXT NOT NULL UNIQUE,
  description   TEXT,
  archived      INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE TABLE prompt_versions (
  id              INTEGER PRIMARY KEY,
  prompt_id       INTEGER NOT NULL REFERENCES prompts(id) ON DELETE CASCADE,
  version_num     INTEGER NOT NULL,
  state           TEXT NOT NULL CHECK (state IN ('draft','production','archived')),
  body            TEXT NOT NULL,
  target_map      TEXT NOT NULL,
  output_schema   TEXT NOT NULL,
  model           TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL,
  UNIQUE (prompt_id, version_num)
);

CREATE UNIQUE INDEX idx_one_prod_per_prompt
  ON prompt_versions(prompt_id) WHERE state = 'production';

CREATE INDEX idx_prompt_versions_prompt ON prompt_versions(prompt_id);

-- Backfill: each templates row -> prompts + v1@production.
INSERT INTO prompts (id, name, description, archived, created_at, updated_at)
  SELECT id, name, description, archived, created_at, updated_at FROM templates;

INSERT INTO prompt_versions
  (prompt_id, version_num, state, body, target_map, output_schema, model, created_at, updated_at)
  SELECT id, 1, 'production', prompt, target_map, output_schema, model, created_at, updated_at
  FROM templates;

-- Rebuild annotations: template_id -> prompt_version_id.
-- (SQLite < 3.35 cannot drop columns; build new table, copy, swap.)
-- NOTE: The JOIN below silently drops orphan annotations (no matching template).
CREATE TABLE annotations_new (
  id                 INTEGER PRIMARY KEY,
  catdv_clip_id      INTEGER NOT NULL,
  catdv_clip_name    TEXT NOT NULL,
  prompt_version_id  INTEGER NOT NULL REFERENCES prompt_versions(id),
  job_id             INTEGER REFERENCES jobs(id),
  model              TEXT NOT NULL,
  prompt_used        TEXT NOT NULL,
  raw_response       TEXT NOT NULL,
  structured_output  TEXT NOT NULL,
  clip_snapshot      TEXT NOT NULL,
  created_at         TEXT NOT NULL
);
INSERT INTO annotations_new
  SELECT a.id, a.catdv_clip_id, a.catdv_clip_name,
         pv.id, a.job_id, a.model, a.prompt_used, a.raw_response,
         a.structured_output, a.clip_snapshot, a.created_at
  FROM annotations a
  JOIN prompt_versions pv ON pv.prompt_id = a.template_id AND pv.version_num = 1;

-- Drop FTS + triggers tied to the old annotations table.
DROP TRIGGER IF EXISTS annotations_ai;
DROP TRIGGER IF EXISTS annotations_ad;
DROP TABLE IF EXISTS annotations_fts;
DROP TABLE annotations;
ALTER TABLE annotations_new RENAME TO annotations;

CREATE INDEX idx_annotations_clip ON annotations(catdv_clip_id);
CREATE INDEX idx_annotations_prompt_version ON annotations(prompt_version_id);

CREATE VIRTUAL TABLE annotations_fts USING fts5(
  clip_name, prompt_used, structured_output, raw_response,
  content='annotations', content_rowid='id',
  tokenize = "unicode61 remove_diacritics 2"
);

CREATE TRIGGER annotations_ai AFTER INSERT ON annotations BEGIN
  INSERT INTO annotations_fts(rowid, clip_name, prompt_used, structured_output, raw_response)
  VALUES (new.id, new.catdv_clip_name, new.prompt_used, new.structured_output, new.raw_response);
END;

CREATE TRIGGER annotations_ad AFTER DELETE ON annotations BEGIN
  INSERT INTO annotations_fts(annotations_fts, rowid, clip_name, prompt_used, structured_output, raw_response)
  VALUES ('delete', old.id, old.catdv_clip_name, old.prompt_used, old.structured_output, old.raw_response);
END;

-- Bulk-populate FTS for all pre-migration annotations (INSERT...SELECT does not fire triggers).
INSERT INTO annotations_fts(rowid, clip_name, prompt_used, structured_output, raw_response)
  SELECT id, catdv_clip_name, prompt_used, structured_output, raw_response FROM annotations;

-- Rebuild jobs: template_id -> prompt_version_id.
CREATE TABLE jobs_new (
  id              INTEGER PRIMARY KEY,
  prompt_version_id INTEGER NOT NULL REFERENCES prompt_versions(id),
  status          TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  started_at      TEXT,
  finished_at     TEXT,
  total_clips     INTEGER NOT NULL,
  notes           TEXT
);
INSERT INTO jobs_new (id, prompt_version_id, status, created_at, started_at, finished_at, total_clips, notes)
  SELECT j.id, pv.id, j.status, j.created_at, j.started_at, j.finished_at, j.total_clips, j.notes
  FROM jobs j
  JOIN prompt_versions pv ON pv.prompt_id = j.template_id AND pv.version_num = 1;
DROP TABLE jobs;
ALTER TABLE jobs_new RENAME TO jobs;

DROP TABLE templates;

PRAGMA foreign_keys=ON;
