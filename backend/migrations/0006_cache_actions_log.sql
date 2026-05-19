-- PR 6: audit log for cache evictions and LRU sweeps.
--
-- Every mutation through CacheActions (and every LRU eviction) appends a
-- row here, including skips with a reason. `who` is "system" for LRU and
-- a request-scoped identifier (today: literal "request") for user-driven
-- routes; future auth can replace the latter without a schema change.
--
-- `clip_keys` is a JSON array of [provider_id, provider_clip_id] pairs;
-- single-clip actions still write a one-element array for shape stability.

CREATE TABLE cache_actions_log (
  id          INTEGER PRIMARY KEY,
  who         TEXT NOT NULL,
  action      TEXT NOT NULL,
  clip_keys   TEXT NOT NULL,
  result      TEXT NOT NULL,
  detail      TEXT,
  bytes_freed INTEGER NOT NULL DEFAULT 0,
  at          TEXT NOT NULL
);

CREATE INDEX idx_cache_actions_log_at ON cache_actions_log(at);
