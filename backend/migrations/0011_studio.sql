-- 0011: Prompt Studio — testbenches + runs.

CREATE TABLE testbenches (
  id           INTEGER PRIMARY KEY,
  name         TEXT NOT NULL UNIQUE,
  description  TEXT,
  archived     INTEGER NOT NULL DEFAULT 0,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);

CREATE TABLE testbench_folders (
  id            INTEGER PRIMARY KEY,
  testbench_id  INTEGER NOT NULL REFERENCES testbenches(id) ON DELETE CASCADE,
  parent_id     INTEGER REFERENCES testbench_folders(id) ON DELETE CASCADE,
  name          TEXT NOT NULL,
  sort_index    INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL,
  UNIQUE (testbench_id, parent_id, name)
);
CREATE INDEX idx_tb_folders_parent ON testbench_folders(parent_id);

CREATE TABLE testbench_items (
  id                     INTEGER PRIMARY KEY,
  folder_id              INTEGER NOT NULL REFERENCES testbench_folders(id) ON DELETE CASCADE,
  source_kind            TEXT NOT NULL CHECK (source_kind IN ('upload','catdv_clip')),
  upload_path            TEXT,
  upload_orig_name       TEXT,
  catdv_provider_clip_id TEXT,
  display_name           TEXT NOT NULL,
  gold_json              TEXT,
  sort_index             INTEGER NOT NULL DEFAULT 0,
  created_at             TEXT NOT NULL,
  CHECK (
    (source_kind = 'upload'     AND upload_path IS NOT NULL AND catdv_provider_clip_id IS NULL) OR
    (source_kind = 'catdv_clip' AND catdv_provider_clip_id IS NOT NULL AND upload_path IS NULL)
  )
);
CREATE INDEX idx_tb_items_folder ON testbench_items(folder_id);

CREATE TABLE studio_runs (
  id                INTEGER PRIMARY KEY,
  testbench_id      INTEGER NOT NULL REFERENCES testbenches(id),
  prompt_version_id INTEGER NOT NULL REFERENCES prompt_versions(id),
  status            TEXT NOT NULL CHECK (status IN ('pending','running','completed','failed','cancelled')),
  created_at        TEXT NOT NULL,
  started_at        TEXT,
  finished_at       TEXT,
  notes             TEXT
);
CREATE INDEX idx_studio_runs_testbench ON studio_runs(testbench_id, created_at DESC);

CREATE TABLE studio_run_items (
  id                  INTEGER PRIMARY KEY,
  run_id              INTEGER NOT NULL REFERENCES studio_runs(id) ON DELETE CASCADE,
  testbench_item_id   INTEGER NOT NULL REFERENCES testbench_items(id),
  status              TEXT NOT NULL CHECK (status IN (
                        'pending','resolving','uploading','prompting',
                        'done','error','unacceptable')),
  error               TEXT,
  unacceptable_reason TEXT,
  structured_json     TEXT,
  raw_text            TEXT,
  prompt_used         TEXT,
  model               TEXT,
  latency_ms          INTEGER,
  started_at          TEXT,
  finished_at         TEXT,
  UNIQUE (run_id, testbench_item_id)
);
CREATE INDEX idx_studio_run_items_run ON studio_run_items(run_id);
