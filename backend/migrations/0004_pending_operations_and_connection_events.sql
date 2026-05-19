-- PR 4: durable journal for upstream writes + connection-state audit.

CREATE TABLE pending_operations (
  id                     INTEGER PRIMARY KEY,
  provider_id            TEXT NOT NULL,
  provider_clip_id       TEXT NOT NULL,
  op_kind                TEXT NOT NULL,
  op_json                TEXT NOT NULL,
  origin_annotation_id   INTEGER REFERENCES annotations(id),
  origin_review_item_ids TEXT,
  expected_etag          TEXT,
  status                 TEXT NOT NULL,
  attempts               INTEGER NOT NULL DEFAULT 0,
  last_error             TEXT,
  enqueued_at            TEXT NOT NULL,
  attempted_at           TEXT,
  applied_at             TEXT
);
CREATE INDEX idx_pending_ops_status ON pending_operations(status, enqueued_at);

CREATE TABLE connection_events (
  id      INTEGER PRIMARY KEY,
  state   TEXT NOT NULL,
  detail  TEXT,
  at      TEXT NOT NULL
);
