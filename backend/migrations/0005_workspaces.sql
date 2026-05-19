-- PR 5: workspaces + workspace_clips, and the FK from
-- clip_cache.pinned_to_workspace_id → workspaces(id).
--
-- SQLite cannot add a foreign key to an existing column via ALTER TABLE,
-- so clip_cache is rebuilt: rename old → create new (with FK) → copy
-- rows → drop old → re-create the catalog index. This preserves all
-- existing rows.

-- Named pinned working sets.
CREATE TABLE workspaces (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,
  provider_id TEXT NOT NULL,
  catalog_id  TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  description TEXT
);

-- workspace ↔ clip membership; one clip can belong to multiple workspaces.
CREATE TABLE workspace_clips (
  workspace_id      INTEGER NOT NULL
                     REFERENCES workspaces(id) ON DELETE CASCADE,
  provider_id       TEXT NOT NULL,
  provider_clip_id  TEXT NOT NULL,
  added_at          TEXT NOT NULL,
  cache_state       TEXT NOT NULL,    -- pending | metadata | media | ready | error
  cache_error       TEXT,
  PRIMARY KEY (workspace_id, provider_id, provider_clip_id)
);
CREATE INDEX idx_workspace_clips_clip
  ON workspace_clips(provider_id, provider_clip_id);

-- Rebuild clip_cache to attach the FK on pinned_to_workspace_id.
ALTER TABLE clip_cache RENAME TO clip_cache_old;
DROP INDEX IF EXISTS idx_clip_cache_catalog;

CREATE TABLE clip_cache (
  provider_id            TEXT NOT NULL,
  provider_clip_id       TEXT NOT NULL,
  name                   TEXT NOT NULL,
  catalog_id             TEXT NOT NULL,
  duration_secs          REAL NOT NULL,
  fps                    REAL NOT NULL,
  canonical_json         TEXT NOT NULL,
  provider_etag          TEXT,
  fetched_at             TEXT NOT NULL,
  pinned_to_workspace_id INTEGER
    REFERENCES workspaces(id) ON DELETE SET NULL,
  PRIMARY KEY (provider_id, provider_clip_id)
);
CREATE INDEX idx_clip_cache_catalog ON clip_cache(provider_id, catalog_id);

INSERT INTO clip_cache
  (provider_id, provider_clip_id, name, catalog_id, duration_secs, fps,
   canonical_json, provider_etag, fetched_at, pinned_to_workspace_id)
SELECT
   provider_id, provider_clip_id, name, catalog_id, duration_secs, fps,
   canonical_json, provider_etag, fetched_at, pinned_to_workspace_id
FROM clip_cache_old;

DROP TABLE clip_cache_old;
