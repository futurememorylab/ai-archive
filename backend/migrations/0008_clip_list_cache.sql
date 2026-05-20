-- Cache for clip list-page responses. The `/` page hits CatDV every render
-- over a slow VPN; this table lets the adapter serve the same (catalog,
-- query, offset, limit) tuple from SQLite within a short TTL.
--
-- `items_json` stores the full canonical clip payloads for the page, using
-- the same per-clip JSON shape as the `clip_cache` table (see
-- `_clip_to_json` in `backend/app/repositories/clip_cache.py`). `total` is
-- the upstream `totalItems` so paging UI keeps working from the cache.

CREATE TABLE clip_list_cache (
  provider_id  TEXT NOT NULL,
  catalog_id   TEXT NOT NULL,
  query_text   TEXT NOT NULL,            -- '' when no text filter
  offset_      INTEGER NOT NULL,
  limit_       INTEGER NOT NULL,
  total        INTEGER NOT NULL,
  items_json   TEXT NOT NULL,
  fetched_at   TEXT NOT NULL,
  PRIMARY KEY (provider_id, catalog_id, query_text, offset_, limit_)
);

CREATE INDEX idx_clip_list_cache_catalog
  ON clip_list_cache(provider_id, catalog_id, fetched_at);
