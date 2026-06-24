-- backend/migrations/0027_drop_live_search_calls.sql
-- Remove the dead search_calls column from live_sessions (ADR 0109).
-- The Live refactor stopped tracking web-search calls in the browser, so the
-- column only ever stored 0; the model field, route payload/response and repo
-- plumbing were removed, leaving this column written nowhere and read nowhere.
-- googleSearch (the capability) stays; only the unused metric column goes.
-- Requires SQLite >= 3.35 for ALTER TABLE ... DROP COLUMN (prod is far newer).
ALTER TABLE live_sessions DROP COLUMN search_calls;
