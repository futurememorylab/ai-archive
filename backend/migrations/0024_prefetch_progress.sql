-- Issue #78: record total proxy size so the UI can show download progress.
-- bytes_downloaded already exists (0007); add the denominator. 0 = unknown
-- (no Content-Length) → the UI shows no percentage.
ALTER TABLE prefetch_queue ADD COLUMN bytes_total INTEGER NOT NULL DEFAULT 0;
