"""The clip chat service: what Gemini is sent each turn, and the summary."""

import json
from pathlib import Path

import aiosqlite
import pytest

from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.live_sessions import LiveSessionsRepo
from backend.app.services.live_sessions import (
    FRAME_CUE_CS,
    OPENING_CUE_CS,
    build_system_instruction,
    conversation_turns,
    summarize,
)

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


class _FakeGemini:
    """Stands in for GeminiService.chat: records each call, returns `text`."""

    def __init__(self, text: str = "Krátké české shrnutí.") -> None:
        self.text = text
        self.calls: list[dict] = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        return {"text": self.text, "raw": {"usageMetadata": {"promptTokenCount": 10}}}


def test_system_instruction_carries_prompt_and_clip_context():
    clip = {"name": "Výlet", "fields": {}, "markers": []}
    out = build_system_instruction(prompt_body="Jsi asistent.", clip=clip, draft={})
    assert out.startswith("Jsi asistent.")
    assert len(out) > len("Jsi asistent.")


def test_turns_replay_the_transcript_and_add_the_new_question_with_its_frame():
    transcript = [
        {"role": "model", "text": "Dobrý den, vidím zahradu."},
        {"role": "user", "text": "Z kterého je to desetiletí?", "kind": "typed"},
        {"role": "model", "text": "Spíš třicátá léta."},
        {"role": "system", "text": "a note", "kind": "note"},
    ]
    turns = conversation_turns(transcript, question="A kde?", image_jpeg=b"jpeg")
    assert [t["role"] for t in turns] == ["user", "model", "user", "model", "user"]
    assert turns[0]["text"] == OPENING_CUE_CS
    assert turns[-1] == {"role": "user", "text": "A kde?", "image_jpeg": b"jpeg"}
    # Earlier frames are not re-sent: only the new turn carries an image.
    assert all(not t.get("image_jpeg") for t in turns[:-1])


def test_a_sent_frame_replays_as_the_frame_question_not_its_label():
    transcript = [
        {"role": "model", "text": "Dobrý den."},
        {"role": "user", "text": "Frame at 0:12", "kind": "frame"},
        {"role": "model", "text": "Na snímku je auto."},
    ]
    turns = conversation_turns(transcript, question="Jaké?", image_jpeg=None)
    assert turns[2]["text"] == FRAME_CUE_CS


async def _ended_session(conn, transcript: list[dict]) -> LiveSessionsRepo:
    repo = LiveSessionsRepo()
    await repo.insert_pending(conn, id="abc", clip_id=1, prompt_version=None)
    await repo.mark_active(conn, "abc")
    await repo.mark_ended(
        conn,
        "abc",
        end_reason="user_stop",
        transcript_json=json.dumps(transcript, ensure_ascii=False),
    )
    return repo


@pytest.mark.asyncio
async def test_summarize_asks_gemini_for_a_czech_summary(tmp_path):
    async with aiosqlite.connect(tmp_path / "t.db") as conn:
        await apply_migrations(conn, MIGRATIONS)
        repo = await _ended_session(
            conn,
            [
                {"role": "user", "text": "co to je za auto?", "ts": 1},
                {"role": "model", "text": "Vypadá to jako Škoda z 30. let.", "ts": 2},
            ],
        )
        gemini = _FakeGemini()
        ok = await summarize(conn, session_id="abc", gemini=gemini, model="m")
        assert ok is True
        (call,) = gemini.calls
        assert "Shrň" in call["system_instruction"]
        assert "Škoda" in call["turns"][0]["text"]
        assert call["google_search"] is False
        assert (await repo.get(conn, "abc")).summary_cs == "Krátké české shrnutí."


@pytest.mark.asyncio
async def test_summarize_is_idempotent(tmp_path):
    async with aiosqlite.connect(tmp_path / "t.db") as conn:
        await apply_migrations(conn, MIGRATIONS)
        repo = await _ended_session(conn, [{"role": "user", "text": "x", "ts": 1}])
        await repo.set_summary(conn, "abc", "Již existující shrnutí.")
        gemini = _FakeGemini("new")
        assert await summarize(conn, session_id="abc", gemini=gemini, model="m") is False
        assert gemini.calls == []
        assert (await repo.get(conn, "abc")).summary_cs == "Již existující shrnutí."


@pytest.mark.asyncio
async def test_summarize_skips_when_transcript_empty(tmp_path):
    async with aiosqlite.connect(tmp_path / "t.db") as conn:
        await apply_migrations(conn, MIGRATIONS)
        repo = await _ended_session(conn, [])
        gemini = _FakeGemini()
        assert await summarize(conn, session_id="abc", gemini=gemini, model="m") is False
        assert gemini.calls == []
        assert (await repo.get(conn, "abc")).summary_cs is None
