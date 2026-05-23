-- 0010: live_sessions — Gemini Live API conversation records per clip.

CREATE TABLE live_sessions (
  id              TEXT PRIMARY KEY,            -- uuid v4 chosen by backend
  clip_id         INTEGER NOT NULL,
  prompt_version  INTEGER,                     -- which live.system_instruction.cs version was used
  state           TEXT NOT NULL,               -- pending | active | ended | failed
  started_at      TEXT,                        -- iso8601 utc
  ended_at        TEXT,                        -- iso8601 utc
  end_reason      TEXT,                        -- user_stop | voice_stop | inactivity | navigate | error
  transcript_json TEXT,                        -- json array of {role,text,ts,kind}
  summary_cs      TEXT,                        -- czech summary produced post-session
  frame_count     INTEGER NOT NULL DEFAULT 0,
  search_calls    INTEGER NOT NULL DEFAULT 0,
  created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX ix_live_sessions_clip
  ON live_sessions (clip_id, created_at DESC);
