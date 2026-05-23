from datetime import datetime, timezone

import pytest

from backend.app.models.live_session import LiveSession


def test_live_session_minimal_construct():
    s = LiveSession(id="abc", clip_id=42, state="pending")
    assert s.id == "abc"
    assert s.clip_id == 42
    assert s.state == "pending"
    assert s.frame_count == 0
    assert s.search_calls == 0
    assert s.transcript_json is None
    assert s.summary_cs is None


def test_live_session_invalid_state_rejected():
    with pytest.raises(ValueError):
        LiveSession(id="x", clip_id=1, state="bogus")


def test_live_session_invalid_end_reason_rejected():
    with pytest.raises(ValueError):
        LiveSession(id="x", clip_id=1, state="ended", end_reason="nope")


def test_live_session_full_construct_roundtrip():
    now = datetime.now(timezone.utc).isoformat()
    s = LiveSession(
        id="abc", clip_id=42, prompt_version=3, state="ended",
        started_at=now, ended_at=now, end_reason="user_stop",
        transcript_json='[{"role":"user","text":"ahoj","ts":1}]',
        summary_cs="Krátký test.",
        frame_count=2, search_calls=1, created_at=now,
    )
    assert s.end_reason == "user_stop"
    assert s.frame_count == 2
