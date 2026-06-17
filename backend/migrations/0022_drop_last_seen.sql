-- backend/migrations/0022_drop_last_seen.sql
-- Remove the last-seen tracking column from user_roles (ADR 0090).
-- "Last sign-in" was cosmetic bookkeeping surfaced in the admin console; it
-- was removed from the UI and the backend. The invited→active first-sight flip
-- that used to ride along in mark_seen survives as
-- UserRolesRepo.activate_on_first_sight (it no longer needs this column).
-- Requires SQLite >= 3.35 for ALTER TABLE ... DROP COLUMN (prod is far newer).
ALTER TABLE user_roles DROP COLUMN last_seen_at;
