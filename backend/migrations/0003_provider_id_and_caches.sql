-- PR 3: provider-aware clip identity columns + local mirror tables.
-- For each clip-keyed table, add (provider_id, provider_clip_id) and
-- backfill from the existing catdv_clip_id column. The catdv_clip_id
-- column itself is kept until a post-cutover migration drops it.

ALTER TABLE annotations    ADD COLUMN provider_id TEXT;
ALTER TABLE annotations    ADD COLUMN provider_clip_id TEXT;
UPDATE annotations
   SET provider_id = 'catdv',
       provider_clip_id = CAST(catdv_clip_id AS TEXT);

ALTER TABLE review_items   ADD COLUMN provider_id TEXT;
ALTER TABLE review_items   ADD COLUMN provider_clip_id TEXT;
UPDATE review_items
   SET provider_id = 'catdv',
       provider_clip_id = CAST(catdv_clip_id AS TEXT);

ALTER TABLE job_items      ADD COLUMN provider_id TEXT;
ALTER TABLE job_items      ADD COLUMN provider_clip_id TEXT;
UPDATE job_items
   SET provider_id = 'catdv',
       provider_clip_id = CAST(catdv_clip_id AS TEXT);

ALTER TABLE proxy_cache    ADD COLUMN provider_id TEXT;
ALTER TABLE proxy_cache    ADD COLUMN provider_clip_id TEXT;
UPDATE proxy_cache
   SET provider_id = 'catdv',
       provider_clip_id = CAST(catdv_clip_id AS TEXT);

ALTER TABLE ai_store_files ADD COLUMN provider_id TEXT;
ALTER TABLE ai_store_files ADD COLUMN provider_clip_id TEXT;
UPDATE ai_store_files
   SET provider_id = 'catdv',
       provider_clip_id = CAST(catdv_clip_id AS TEXT);

ALTER TABLE write_log      ADD COLUMN provider_id TEXT;
ALTER TABLE write_log      ADD COLUMN provider_clip_id TEXT;
UPDATE write_log
   SET provider_id = 'catdv',
       provider_clip_id = CAST(catdv_clip_id AS TEXT);

-- Local mirror of upstream clip state. PR 5 will add the FK on
-- pinned_to_workspace_id once the workspaces table exists.
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
  pinned_to_workspace_id INTEGER,
  PRIMARY KEY (provider_id, provider_clip_id)
);
CREATE INDEX idx_clip_cache_catalog ON clip_cache(provider_id, catalog_id);

-- Local mirror of provider field definitions.
CREATE TABLE field_def_cache (
  provider_id  TEXT NOT NULL,
  identifier   TEXT NOT NULL,
  json         TEXT NOT NULL,
  fetched_at   TEXT NOT NULL,
  PRIMARY KEY (provider_id, identifier)
);
