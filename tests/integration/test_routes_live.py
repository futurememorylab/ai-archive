"""Clip chat routes: start, ask, end, summarize, history.

Gemini is a fake standing in for GeminiService.chat (Vertex); nothing here
talks to Google."""

import base64
import json
from pathlib import Path

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.main import app
from backend.app.migrations_runner import apply_migrations
from backend.app.models.telemetry import TelemetryCtx
from backend.app.repositories.live_sessions import LiveSessionsRepo
from backend.app.repositories.run_telemetry import RunTelemetryRepo
from backend.app.seed import seed_system_instruction
from backend.app.services.gemini import GeminiQuotaError
from backend.app.services.live_sessions import FRAME_CUE_CS, OPENING_CUE_CS

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"
SEEDS = Path(__file__).resolve().parents[2] / "backend" / "seeds"
FRAME = base64.b64encode(b"\xff\xd8jpeg").decode()


class _FakeGemini:
    """Answers each chat call with the next reply; records what it was sent."""

    def __init__(self) -> None:
        self.replies = ["Dobrý den, vidím zahradu.", "Spíš třicátá léta.", "Auto."]
        self.calls: list[dict] = []
        self.fail: Exception | None = None

    async def chat(self, **kwargs):
        if self.fail:
            raise self.fail
        self.calls.append(kwargs)
        return {
            "text": self.replies.pop(0),
            "raw": {"usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20}},
        }


@pytest.fixture
async def chat(tmp_path, monkeypatch):
    """The app with a fresh sqlite db, the chat prompt seeded and a fake Gemini."""
    import backend.app.routes.live as live_routes

    conn = await aiosqlite.connect(tmp_path / "t.db")
    await apply_migrations(conn, MIGRATIONS)
    await seed_system_instruction(conn, seed_path=SEEDS / "chat_system_instruction_cs.json")

    async def fake_load_clip(ctx, clip_id):
        return dict(id=clip_id, name="P1010001", notes="rodinný výlet", markers=[], fields={})

    async def fake_load_draft(ctx, clip_id):
        return dict(markers=[], fields={}, notes="")

    monkeypatch.setattr(live_routes, "load_clip_for_live", fake_load_clip)
    monkeypatch.setattr(live_routes, "load_draft_for_live", fake_load_draft)

    class _Ctx:
        db = conn
        mode = "online"
        gemini = _FakeGemini()
        run_telemetry_repo = RunTelemetryRepo()
        telemetry_ctx = TelemetryCtx(install_id="inst-test")
        settings = type(
            "S",
            (),
            {
                "gemini_chat_model": "gemini-3.8-flash",
                "gemini_model": "gemini-3.5-flash-lite",
                # Read by the _attach_current_user middleware on every request.
                "auth_backend": "dev",
                "dev_user_email": "dev@localhost",
            },
        )()

    ctx = _Ctx()
    app.state.core_ctx = ctx
    app.state.live_ctx = ctx
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, conn, ctx.gemini
    await conn.close()


async def _start(ac) -> str:
    r = await ac.post("/api/live/sessions", json={"clip_id": 42, "frame_b64": FRAME})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


async def _telemetry(conn):
    cur = await conn.execute("SELECT kind, model, tokens_in, attrs FROM run_telemetry")
    return await cur.fetchall()


@pytest.mark.asyncio
async def test_start_greets_about_the_frame_on_screen(chat):
    ac, conn, gemini = chat
    r = await ac.post("/api/live/sessions", json={"clip_id": 42, "frame_b64": FRAME})
    assert r.status_code == 200, r.text
    assert r.json()["reply"] == "Dobrý den, vidím zahradu."
    (call,) = gemini.calls
    assert call["model"] == "gemini-3.8-flash"
    assert call["google_search"] is False  # the greeting needs no web search
    # The system prompt carries the clip's context; the opening turn the frame.
    assert "rodinný výlet" in call["system_instruction"]
    assert call["turns"] == [
        {"role": "user", "text": OPENING_CUE_CS, "image_jpeg": b"\xff\xd8jpeg"}
    ]
    session = await LiveSessionsRepo().get(conn, r.json()["session_id"])
    assert session.state == "active"
    assert session.frame_count == 1
    assert json.loads(session.transcript_json or "[]")[0]["text"] == "Dobrý den, vidím zahradu."


@pytest.mark.asyncio
async def test_a_question_carries_the_conversation_and_is_saved(chat):
    ac, conn, gemini = chat
    sid = await _start(ac)
    r = await ac.post(
        f"/api/live/sessions/{sid}/turns",
        json={"kind": "typed", "text": "Z kterého je to desetiletí?", "frame_b64": FRAME},
    )
    assert r.status_code == 200, r.text
    assert r.json()["reply"] == "Spíš třicátá léta."
    turns = gemini.calls[-1]["turns"]
    assert [t["text"] for t in turns] == [
        OPENING_CUE_CS,
        "Dobrý den, vidím zahradu.",
        "Z kterého je to desetiletí?",
    ]
    assert turns[-1]["image_jpeg"] == b"\xff\xd8jpeg"
    assert gemini.calls[-1]["google_search"] is True
    session = await LiveSessionsRepo().get(conn, sid)
    transcript = json.loads(session.transcript_json or "[]")
    assert [(t["role"], t["text"]) for t in transcript[1:]] == [
        ("user", "Z kterého je to desetiletí?"),
        ("model", "Spíš třicátá léta."),
    ]
    assert session.frame_count == 2


@pytest.mark.asyncio
async def test_send_frame_asks_about_the_frame_and_shows_its_label(chat):
    ac, conn, gemini = chat
    sid = await _start(ac)
    r = await ac.post(
        f"/api/live/sessions/{sid}/turns",
        json={"kind": "frame", "text": "Frame at 0:12", "frame_b64": FRAME},
    )
    assert r.status_code == 200, r.text
    assert gemini.calls[-1]["turns"][-1]["text"] == FRAME_CUE_CS
    transcript = json.loads((await LiveSessionsRepo().get(conn, sid)).transcript_json or "[]")
    assert transcript[1] == {**transcript[1], "text": "Frame at 0:12", "kind": "frame"}


@pytest.mark.asyncio
async def test_each_answer_records_its_cost(chat):
    ac, conn, _ = chat
    sid = await _start(ac)
    await ac.post(f"/api/live/sessions/{sid}/turns", json={"text": "A kde?"})
    rows = await _telemetry(conn)
    assert [json.loads(r[3])["call"] for r in rows] == ["chat_open", "chat_turn"]
    assert all(r[0] == "live" and r[1] == "gemini-3.8-flash" and r[2] == 100 for r in rows)


@pytest.mark.asyncio
async def test_gemini_failure_is_a_502_with_the_reason(chat):
    ac, conn, gemini = chat
    sid = await _start(ac)
    gemini.fail = GeminiQuotaError("429 resource exhausted")
    r = await ac.post(f"/api/live/sessions/{sid}/turns", json={"text": "A kde?"})
    assert r.status_code == 502
    assert "resource exhausted" in r.json()["detail"]
    # The failed question is not saved; the chat stays usable.
    session = await LiveSessionsRepo().get(conn, sid)
    assert session.state == "active"
    assert len(json.loads(session.transcript_json or "[]")) == 1


@pytest.mark.asyncio
async def test_a_failed_start_ends_the_session(chat):
    ac, conn, gemini = chat
    gemini.fail = GeminiQuotaError("429 resource exhausted")
    r = await ac.post("/api/live/sessions", json={"clip_id": 42})
    assert r.status_code == 502
    (session,) = await LiveSessionsRepo().list_by_clip(conn, 42)
    assert (session.state, session.end_reason) == ("ended", "error")


@pytest.mark.asyncio
async def test_bad_frame_and_empty_question_are_rejected(chat):
    ac, _, _ = chat
    sid = await _start(ac)
    r = await ac.post(
        f"/api/live/sessions/{sid}/turns", json={"text": "x", "frame_b64": "not base64!"}
    )
    assert r.status_code == 422
    r = await ac.post(f"/api/live/sessions/{sid}/turns", json={"text": "  "})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_end_keeps_the_transcript_and_refuses_more_questions(chat):
    ac, conn, _ = chat
    sid = await _start(ac)
    r = await ac.post(f"/api/live/sessions/{sid}/end", json={"end_reason": "user_stop"})
    assert r.status_code == 200
    # A navigate beacon after End chat changes nothing.
    await ac.post(f"/api/live/sessions/{sid}/end", json={"end_reason": "navigate"})
    session = await LiveSessionsRepo().get(conn, sid)
    assert (session.state, session.end_reason) == ("ended", "user_stop")
    assert json.loads(session.transcript_json or "[]")[0]["role"] == "model"
    r = await ac.post(f"/api/live/sessions/{sid}/turns", json={"text": "Ještě?"})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_unknown_session_404(chat):
    ac, _, _ = chat
    r = await ac.post("/api/live/sessions/no-such/turns", json={"text": "x"})
    assert r.status_code == 404
    r = await ac.post("/api/live/sessions/no-such/end", json={})
    assert r.status_code == 404
    r = await ac.get("/api/live/sessions/no-such")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_summarize_stores_the_summary_and_its_cost(chat):
    ac, conn, gemini = chat
    sid = await _start(ac)
    await ac.post(f"/api/live/sessions/{sid}/end", json={})
    gemini.replies = ["Krátké shrnutí."]
    r = await ac.post(f"/api/live/sessions/{sid}/summarize")
    assert r.status_code == 200, r.text
    assert r.json()["summary_cs"] == "Krátké shrnutí."
    assert gemini.calls[-1]["model"] == "gemini-3.5-flash-lite"
    rows = await _telemetry(conn)
    assert json.loads(rows[-1][3])["call"] == "summary"
    # Idempotent: a second summarize doesn't call Gemini again.
    calls = len(gemini.calls)
    r = await ac.post(f"/api/live/sessions/{sid}/summarize")
    assert r.json()["summary_cs"] == "Krátké shrnutí."
    assert len(gemini.calls) == calls


@pytest.mark.asyncio
async def test_history_lists_and_shows_sessions(chat):
    ac, conn, _ = chat
    sid = await _start(ac)
    await ac.post(f"/api/live/sessions/{sid}/end", json={})
    await LiveSessionsRepo().insert_pending(conn, id="other", clip_id=99, prompt_version=None)
    r = await ac.get("/api/live/sessions", params={"clip_id": 42})
    assert [s["id"] for s in r.json()] == [sid]
    assert r.json()[0]["end_reason"] == "user_stop"
    r = await ac.get(f"/api/live/sessions/{sid}")
    assert r.json()["transcript"][0]["text"] == "Dobrý den, vidím zahradu."
