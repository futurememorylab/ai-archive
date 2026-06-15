-- Issue #55: uploaded-clip GCS object keys are now namespaced per
-- instance (instances/{instance_id}/uploads/{clip_id}.mov). Existing
-- ai_store_files cache rows for uploaded clips still point at the old
-- shared clips/{id}.mov path, so drop them; the next Studio Run
-- re-uploads to the namespaced path (cache-miss -> fetch is the existing
-- contract). Only cache index rows are removed -- uploaded_clip rows and
-- local file copies are untouched. CatDV cache rows (< 1000000000) survive.
DELETE FROM ai_store_files WHERE catdv_clip_id >= 1000000000;
