-- 0032: review_edit_log — an append-only record of every human decision on a
-- draft item (accept / reject / reset / edit), kept for learning the archive's
-- annotation style. review_items is the mutable working draft: edited_value is
-- overwritten on each edit and a re-run or restore DELETEs the un-applied rows,
-- so without this log the human's corrections to a draft that was never
-- published are lost. No FK to review_items for exactly that reason; the
-- prompt / model context is copied onto the row at write time.
-- See docs/specs/2026-10-17-human-edit-and-cost-logging-design.md.
CREATE TABLE review_edit_log (
  id                 INTEGER PRIMARY KEY,
  occurred_at        TEXT    NOT NULL,
  author             TEXT,
  action             TEXT    NOT NULL CHECK (action IN ('decision','bulk_accept','backfill')),
  review_item_id     INTEGER NOT NULL,
  catdv_clip_id      INTEGER NOT NULL,
  annotation_id      INTEGER,
  studio_run_id      INTEGER,
  prompt_version_id  INTEGER,
  model              TEXT,
  kind               TEXT    NOT NULL,
  target_identifier  TEXT,
  proposed_value     TEXT    NOT NULL,
  decision_before    TEXT,
  decision_after     TEXT    NOT NULL,
  edited_before      TEXT,
  edited_after       TEXT
);
CREATE INDEX idx_review_edit_log_clip ON review_edit_log(catdv_clip_id, id);
CREATE INDEX idx_review_edit_log_prompt ON review_edit_log(prompt_version_id);

-- Backfill: the current state of every draft item a human already touched,
-- so decisions made before this table existed are not lost on the next re-run.
INSERT INTO review_edit_log
  (occurred_at, author, action, review_item_id, catdv_clip_id, annotation_id,
   studio_run_id, prompt_version_id, model, kind, target_identifier,
   proposed_value, decision_before, decision_after, edited_before, edited_after)
SELECT
   COALESCE(ri.decided_at, datetime('now')), NULL, 'backfill', ri.id, ri.catdv_clip_id,
   ri.annotation_id, ri.studio_run_id,
   COALESCE(a.prompt_version_id, sr.prompt_version_id), COALESCE(a.model, sr.model),
   ri.kind, ri.target_identifier, ri.proposed_value, NULL, ri.decision, NULL, ri.edited_value
FROM review_items ri
LEFT JOIN annotations a ON a.id = ri.annotation_id
LEFT JOIN studio_run sr ON sr.id = ri.studio_run_id
WHERE ri.decision != 'pending' OR ri.edited_value IS NOT NULL;
