-- 0014: review_items can belong to EITHER an annotation (CatDV-bound) OR a
-- studio_run (Studio iteration, never written to CatDV).
-- SQLite can't ALTER a NOT NULL constraint, so rebuild the table.

-- Provider columns were added by migration 0003 (provider_id /
-- provider_clip_id). Preserve them on the rebuild — dropping them
-- would silently lose backfilled values on every upgrade.
CREATE TABLE review_items_new (
  id                 INTEGER PRIMARY KEY,
  annotation_id      INTEGER REFERENCES annotations(id),
  studio_run_id      INTEGER REFERENCES studio_run(id),
  catdv_clip_id      INTEGER NOT NULL,
  provider_id        TEXT,
  provider_clip_id   TEXT,
  kind               TEXT    NOT NULL,
  target_identifier  TEXT,
  proposed_value     TEXT    NOT NULL,
  edited_value       TEXT,
  decision           TEXT    NOT NULL,
  decided_at         TEXT,
  applied_at         TEXT,
  CHECK ((annotation_id IS NOT NULL AND studio_run_id IS NULL)
      OR (annotation_id IS NULL AND studio_run_id IS NOT NULL))
);

INSERT INTO review_items_new
  (id, annotation_id, studio_run_id, catdv_clip_id, provider_id, provider_clip_id,
   kind, target_identifier,
   proposed_value, edited_value, decision, decided_at, applied_at)
SELECT
   id, annotation_id, NULL,           catdv_clip_id, provider_id, provider_clip_id,
   kind, target_identifier,
   proposed_value, edited_value, decision, decided_at, applied_at
FROM review_items;

DROP TABLE review_items;
ALTER TABLE review_items_new RENAME TO review_items;

CREATE INDEX idx_review_items_annotation ON review_items(annotation_id);
CREATE INDEX idx_review_items_studio_run ON review_items(studio_run_id);
CREATE INDEX idx_review_items_clip       ON review_items(catdv_clip_id);
CREATE INDEX idx_review_items_decision   ON review_items(decision);
