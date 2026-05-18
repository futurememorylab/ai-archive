-- Templates
CREATE TABLE templates (
  id              INTEGER PRIMARY KEY,
  name            TEXT NOT NULL UNIQUE,
  description     TEXT,
  prompt          TEXT NOT NULL,
  output_schema   TEXT NOT NULL,
  target_map      TEXT NOT NULL,
  model           TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL,
  archived        INTEGER NOT NULL DEFAULT 0
);

-- Jobs and items
CREATE TABLE jobs (
  id              INTEGER PRIMARY KEY,
  template_id     INTEGER NOT NULL REFERENCES templates(id),
  status          TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  started_at      TEXT,
  finished_at     TEXT,
  total_clips     INTEGER NOT NULL,
  notes           TEXT
);

CREATE TABLE job_items (
  id              INTEGER PRIMARY KEY,
  job_id          INTEGER NOT NULL REFERENCES jobs(id),
  catdv_clip_id   INTEGER NOT NULL,
  status          TEXT NOT NULL,
  error_message   TEXT,
  annotation_id   INTEGER,
  started_at      TEXT,
  finished_at     TEXT
);
CREATE INDEX idx_job_items_job ON job_items(job_id);
CREATE INDEX idx_job_items_status ON job_items(status);

-- Local proxy file cache (rest mode only)
CREATE TABLE proxy_cache (
  catdv_clip_id   INTEGER PRIMARY KEY,
  file_path       TEXT NOT NULL,
  size_bytes      INTEGER NOT NULL,
  etag            TEXT,
  downloaded_at   TEXT NOT NULL,
  last_used_at    TEXT NOT NULL
);

-- GCS upload registry (reused across re-annotation)
CREATE TABLE gcs_files (
  catdv_clip_id   INTEGER PRIMARY KEY,
  gcs_uri         TEXT NOT NULL,
  mime_type       TEXT NOT NULL,
  size_bytes      INTEGER NOT NULL,
  sha256          TEXT NOT NULL,
  uploaded_at     TEXT NOT NULL,
  last_used_at    TEXT NOT NULL
);

-- Annotation archive
CREATE TABLE annotations (
  id                 INTEGER PRIMARY KEY,
  catdv_clip_id      INTEGER NOT NULL,
  catdv_clip_name    TEXT NOT NULL,
  template_id        INTEGER NOT NULL REFERENCES templates(id),
  job_id             INTEGER REFERENCES jobs(id),
  model              TEXT NOT NULL,
  prompt_used        TEXT NOT NULL,
  raw_response       TEXT NOT NULL,
  structured_output  TEXT NOT NULL,
  clip_snapshot      TEXT NOT NULL,
  created_at         TEXT NOT NULL
);
CREATE INDEX idx_annotations_clip ON annotations(catdv_clip_id);
CREATE INDEX idx_annotations_template ON annotations(template_id);

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

-- Review queue
CREATE TABLE review_items (
  id                 INTEGER PRIMARY KEY,
  annotation_id      INTEGER NOT NULL REFERENCES annotations(id),
  catdv_clip_id      INTEGER NOT NULL,
  kind               TEXT NOT NULL,
  target_identifier  TEXT,
  proposed_value     TEXT NOT NULL,
  edited_value       TEXT,
  decision           TEXT NOT NULL,
  decided_at         TEXT,
  applied_at         TEXT
);
CREATE INDEX idx_review_items_annotation ON review_items(annotation_id);
CREATE INDEX idx_review_items_clip ON review_items(catdv_clip_id);
CREATE INDEX idx_review_items_decision ON review_items(decision);

CREATE TABLE write_log (
  id              INTEGER PRIMARY KEY,
  catdv_clip_id   INTEGER NOT NULL,
  annotation_id   INTEGER REFERENCES annotations(id),
  payload         TEXT NOT NULL,
  response        TEXT NOT NULL,
  status          TEXT NOT NULL,
  written_at      TEXT NOT NULL
);

-- Reserved for future search/curation app
CREATE TABLE embeddings (
  annotation_id   INTEGER PRIMARY KEY REFERENCES annotations(id),
  model           TEXT NOT NULL,
  vector          BLOB NOT NULL,
  created_at      TEXT NOT NULL
);

CREATE TABLE tags (
  annotation_id   INTEGER NOT NULL REFERENCES annotations(id),
  tag             TEXT NOT NULL,
  source          TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  PRIMARY KEY (annotation_id, tag)
);
