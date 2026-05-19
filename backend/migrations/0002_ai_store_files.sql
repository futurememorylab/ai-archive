-- Rename gcs_files -> ai_store_files; introduce store_id column.
-- store_id format is "gcs:<bucket>" for GCS uploads. Backfill by parsing the
-- existing gs:// URI: substring after "gs://" up to the next "/".
-- PK becomes (store_id, catdv_clip_id) so the same clip can have rows in
-- multiple stores (e.g. someone switches AI_INPUT_STORE later).

CREATE TABLE ai_store_files (
  store_id        TEXT NOT NULL,
  catdv_clip_id   INTEGER NOT NULL,
  gcs_uri         TEXT NOT NULL,
  mime_type       TEXT NOT NULL,
  size_bytes      INTEGER NOT NULL,
  sha256          TEXT NOT NULL,
  uploaded_at     TEXT NOT NULL,
  last_used_at    TEXT NOT NULL,
  expires_at      TEXT,
  PRIMARY KEY (store_id, catdv_clip_id)
);

CREATE INDEX idx_ai_store_files_clip ON ai_store_files(catdv_clip_id);

INSERT INTO ai_store_files
  (store_id, catdv_clip_id, gcs_uri, mime_type, size_bytes, sha256,
   uploaded_at, last_used_at, expires_at)
SELECT
  'gcs:' || substr(gcs_uri, 6, instr(substr(gcs_uri, 6), '/') - 1),
  catdv_clip_id,
  gcs_uri,
  mime_type,
  size_bytes,
  sha256,
  uploaded_at,
  last_used_at,
  NULL
FROM gcs_files;

DROP TABLE gcs_files;
