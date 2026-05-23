from typing import Literal

from pydantic import BaseModel

LiveSessionState = Literal["pending", "active", "ended", "failed"]
EndReason = Literal["user_stop", "voice_stop", "inactivity", "navigate", "error"]


class LiveSession(BaseModel):
    id: str
    clip_id: int
    prompt_version: int | None = None
    state: LiveSessionState
    started_at: str | None = None
    ended_at: str | None = None
    end_reason: EndReason | None = None
    transcript_json: str | None = None
    summary_cs: str | None = None
    frame_count: int = 0
    search_calls: int = 0
    created_at: str | None = None
