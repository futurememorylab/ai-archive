-- backend/migrations/0018_uploaded_clips.sql
-- 0018: uploaded studio clips. A row here is a user-uploaded video that
-- participates in the studio pipeline via a synthetic clip_id
-- (UPLOAD_ID_BASE + id; see backend/app/uploaded_ids.py). Set
-- membership lives in studio_set_clip exactly as for archive clips; this
-- table holds only the per-upload metadata the navigator + run path need.
-- AUTOINCREMENT guarantees ids are never reused, so a deleted upload's
-- synthetic clip_id can't later collide with a different file.
CREATE TABLE uploaded_clip (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  original_filename TEXT    NOT NULL,
  stored_filename   TEXT    NOT NULL,
  mime              TEXT    NOT NULL,
  size_bytes        INTEGER NOT NULL,
  duration_secs     REAL,
  width             INTEGER,
  height            INTEGER,
  created_at        TEXT    NOT NULL
);
