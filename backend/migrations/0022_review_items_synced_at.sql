-- 0022: review_items.synced_at — when a clip's write-back actually LANDS on
-- CatDV (its originating pending_operations row reaches status='applied'), the
-- SyncEngine stamps synced_at on the contributing review_items. applied_at
-- marks ENQUEUE (and is the double-click dedup key); synced_at marks
-- server-confirmation, so the UI can show "applied/synced" only once a change
-- is truly upstream rather than merely queued. See ADR 0093.
ALTER TABLE review_items ADD COLUMN synced_at TEXT;
