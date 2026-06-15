-- 0020: editable enumeration values. Holds ONLY user-editable enums' value
-- edits (the code registry in backend/app/enums/registry.py is canonical for
-- fixed enums and for editable-enum seeds). A row is materialised either by
-- boot-time reconcile (source='seed') or by an admin add (source='user').
-- remove is a soft delete (removed=1) so reconcile won't re-add a value the
-- user deleted. See ADR + spec 2026-06-14-centralised-enumeration-design.md.
CREATE TABLE enum_values (
  enum_key   TEXT    NOT NULL,
  value      TEXT    NOT NULL,
  label      TEXT,
  enabled    INTEGER NOT NULL DEFAULT 1,
  is_default INTEGER NOT NULL DEFAULT 0,
  sort_order INTEGER NOT NULL DEFAULT 0,
  source     TEXT    NOT NULL DEFAULT 'user',
  removed    INTEGER NOT NULL DEFAULT 0,
  metadata   TEXT,
  created_at TEXT    NOT NULL,
  PRIMARY KEY (enum_key, value)
);
CREATE UNIQUE INDEX idx_enum_values_default
  ON enum_values(enum_key) WHERE is_default = 1 AND removed = 0;
CREATE INDEX idx_enum_values_key ON enum_values(enum_key, sort_order);
