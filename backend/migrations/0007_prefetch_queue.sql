-- PR 8: persistent prefetch queue for proxy downloads.
--
-- One row per requested prefetch. The MediaPrefetcher background worker
-- claims rows in `requested_at` order, status='queued' → 'downloading' →
-- 'done' | 'error'. The user may cancel queued/error rows from the
-- /cache page; cancellation of an in-flight row is not supported (see
-- decision 4 in the plan).

CREATE TABLE prefetch_queue (
  id                INTEGER PRIMARY KEY,
  provider_id       TEXT NOT NULL,
  provider_clip_id  TEXT NOT NULL,
  status            TEXT NOT NULL,            -- queued|downloading|done|error|cancelled
  requested_by      TEXT NOT NULL,            -- "request" today; future user id
  requested_at      TEXT NOT NULL,
  started_at        TEXT,
  finished_at       TEXT,
  error             TEXT,
  bytes_downloaded  INTEGER NOT NULL DEFAULT 0
);

-- Worker drains by (status, requested_at).
CREATE INDEX idx_prefetch_queue_status_requested_at
  ON prefetch_queue(status, requested_at);

-- Enqueue de-dup check: do we already have a non-terminal row for this clip?
CREATE INDEX idx_prefetch_queue_clip_status
  ON prefetch_queue(provider_id, provider_clip_id, status);
