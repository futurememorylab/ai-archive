-- 0023: clip_versions — one immutable row per PUBLISH of a clip's annotation
-- state (a commit). History is the list of these rows; review_items remains
-- the working draft. publish_state tracks the row's write to CatDV; exactly
-- one 'live' per clip is enforced in code (supersede-on-flip), not by a
-- partial index, to keep conflict/failed transitions simple. See spec
-- docs/specs/2026-06-17-clip-version-history-design.md.
CREATE TABLE clip_versions (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  provider_id        TEXT    NOT NULL DEFAULT 'catdv',
  catdv_clip_id      INTEGER NOT NULL,
  version_num        INTEGER NOT NULL,
  parent_version_id  INTEGER REFERENCES clip_versions(id),
  snapshot           TEXT    NOT NULL,
  diff               TEXT,
  origin             TEXT    NOT NULL,
  model              TEXT,
  prompt_version_id  INTEGER,
  annotation_id      INTEGER REFERENCES annotations(id),
  author             TEXT,
  publish_state      TEXT    NOT NULL,
  expected_etag      TEXT,
  failed_reason      TEXT,
  synced_at          TEXT,
  created_at         TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX ix_clip_versions_clip
  ON clip_versions(provider_id, catdv_clip_id, version_num DESC);

-- The write-queue hook: which clip_version a pending op publishes, so the
-- SyncEngine can flip that version live when the op lands. Mirrors the
-- existing origin_annotation_id / origin_review_item_ids columns.
ALTER TABLE pending_operations ADD COLUMN origin_clip_version_id INTEGER;
