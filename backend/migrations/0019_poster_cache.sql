-- 0019: lightweight poster-id cache. Maps a clip to its CatDV posterID,
-- populated when listing clips (the list payload carries posterID but not
-- full detail). Lets the thumbnail path fetch a listed clip's poster without
-- a full get_clip — sidestepping the metadata gate (ADR 0065) for clips that
-- have been listed but not opened, WITHOUT polluting clip_cache with partial
-- rows (get_clip is cache-first). See ADR 0072.
CREATE TABLE poster_cache (
  provider_id      TEXT    NOT NULL,
  provider_clip_id TEXT    NOT NULL,
  poster_id        INTEGER NOT NULL,
  updated_at       TEXT    NOT NULL,
  PRIMARY KEY (provider_id, provider_clip_id)
);
