-- 0012: tag each prompt with the media kind it targets so the Annotate
-- dropdown can offer only kind-appropriate prompts. New prompts default to
-- 'any'; existing prompts are video-oriented (the only seed is the
-- scene-marker prompt), so backfill them to 'video'.

ALTER TABLE prompts
  ADD COLUMN media_kind TEXT NOT NULL DEFAULT 'any'
  CHECK (media_kind IN ('video', 'image', 'any'));

UPDATE prompts SET media_kind = 'video';
