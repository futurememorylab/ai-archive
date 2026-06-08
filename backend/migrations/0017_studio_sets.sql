-- backend/migrations/0017_studio_sets.sql
-- 0017: Rename Prompt Studio "folders" to "sets" and add a per-set source
-- discriminator ('archive' | 'uploaded'). See
-- docs/specs/2026-06-08-prompt-studio-sets-navigator-design.md.
--
-- (Planned as 0015 in the implementation plan; bumped to 0017 because the
-- on-disk tree already had 0015_jobs_run_group.sql and 0016_run_telemetry.sql.
-- The runner applies in lexical order, so 0017 is the next free number.)
--
-- studio_folder.name was a COLUMN-LEVEL UNIQUE, which can't be dropped in
-- place, so studio_set is built fresh (adds `source`, changes uniqueness to
-- (source, name)) and rows are copied. studio_folder_clip becomes
-- studio_set_clip (folder_id → set_id), FK repointed to studio_set.
--
-- Drop order is child-before-parent so it is safe whether or not foreign
-- keys are enforced.
PRAGMA foreign_keys = OFF;

CREATE TABLE studio_set (
  id         INTEGER PRIMARY KEY,
  name       TEXT    NOT NULL,
  source     TEXT    NOT NULL DEFAULT 'archive'
                     CHECK (source IN ('archive','uploaded')),
  created_at TEXT    NOT NULL
);

INSERT INTO studio_set (id, name, source, created_at)
  SELECT id, name, 'archive', created_at FROM studio_folder;

CREATE UNIQUE INDEX studio_set_source_name ON studio_set(source, name);

CREATE TABLE studio_set_clip (
  set_id   INTEGER NOT NULL REFERENCES studio_set(id) ON DELETE CASCADE,
  clip_id  INTEGER NOT NULL,
  added_at TEXT    NOT NULL,
  PRIMARY KEY (set_id, clip_id)
);

INSERT INTO studio_set_clip (set_id, clip_id, added_at)
  SELECT folder_id, clip_id, added_at FROM studio_folder_clip;

DROP TABLE studio_folder_clip;
DROP TABLE studio_folder;
