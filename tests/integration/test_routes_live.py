import json
from pathlib import Path

import aiosqlite
import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

from backend.app.main import app
from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.live_sessions import LiveSessionsRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.seed import seed_live_system_instruction

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"
SEEDS = Path(__file__).resolve().parents[2] / "backend" / "seeds"


@pytest.fixture
async def client_and_db(tmp_path):
    """Spin up the FastAPI app with a fresh sqlite db + a stub ctx."""
    db_path = tmp_path / "t.db"
    conn = await aiosqlite.connect(db_path)
    await apply_migrations(conn, MIGRATIONS)
    # Seed the live system-instruction prompt so session-config can find it.
    await seed_live_system_instruction(
        conn, seed_path=SEEDS / "live_system_instruction_cs.json",
    )

    class _Ctx:
        db = conn
        mode = "online"
        settings = type("S", (), {
            "gemini_api_key": "test-key",
            "gemini_live_model": "gemini-2.5-flash-preview-native-audio-dialog",
            "gemini_live_voice": "Aoede",
            "gemini_live_inactivity_s": 60,
            "gemini_model": "gemini-2.5-flash-lite",
        })()
    app.state.ctx = _Ctx()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, conn
    await conn.close()


@pytest.mark.asyncio
@respx.mock
async def test_session_config_returns_token_and_setup(client_and_db, monkeypatch):
    ac, conn = client_and_db
    import backend.app.routes.live as live_routes

    async def fake_load_clip(ctx, clip_id):
        return dict(
            id=clip_id, name="P1010001", format="9,5 mm", fps=25,
            duration_secs=120.0, duration_smpte="00:02:00:00",
            notes="rodinný výlet", big_notes="",
            markers=[], fields={},
        )

    async def fake_load_draft(ctx, clip_id):
        return dict(markers=[], fields={}, notes="")

    monkeypatch.setattr(live_routes, "load_clip_for_live", fake_load_clip)
    monkeypatch.setattr(live_routes, "load_draft_for_live", fake_load_draft)

    respx.post(
        "https://generativelanguage.googleapis.com/v1alpha/auth_tokens"
    ).mock(return_value=Response(200, json={"name": "tokens/xyz"}))

    r = await ac.get("/api/live/session-config", params={"clip_id": 42})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["token"] == "tokens/xyz"
    assert data["session_id"]
    assert data["ws_url"].startswith("wss://generativelanguage.googleapis.com/ws/")
    assert "access_token=tokens/xyz" in data["ws_url"]
    assert data["setup_payload"]["model"].endswith("native-audio-dialog")
    assert data["setup_payload"]["initial_context_turn"]["parts"][0]["text"].startswith(
        "=== Publikované anotace"
    )
    repo = LiveSessionsRepo()
    row = await repo.get(conn, data["session_id"])
    assert row.state == "pending"


@pytest.mark.asyncio
async def test_session_config_404_when_offline(client_and_db):
    ac, _ = client_and_db
    app.state.ctx.mode = "offline"
    r = await ac.get("/api/live/session-config", params={"clip_id": 42})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_transcript_persist_happy_path(client_and_db):
    ac, conn = client_and_db
    repo = LiveSessionsRepo()
    await repo.insert_pending(conn, id="abc", clip_id=42, prompt_version=None)
    await repo.mark_active(conn, "abc")

    payload = {
        "end_reason": "user_stop",
        "transcript": [
            {"role": "user", "text": "ahoj", "ts": 1, "kind": "speech"},
            {"role": "model", "text": "dobrý den", "ts": 2, "kind": "speech"},
        ],
        "frame_count": 3,
        "search_calls": 1,
    }
    r = await ac.post("/api/live/sessions/abc/transcript", json=payload)
    assert r.status_code == 200, r.text
    s = await repo.get(conn, "abc")
    assert s.state == "ended"
    assert s.end_reason == "user_stop"
    assert s.frame_count == 3
    assert json.loads(s.transcript_json) == payload["transcript"]


@pytest.mark.asyncio
async def test_transcript_invalid_end_reason_rejected(client_and_db):
    ac, conn = client_and_db
    repo = LiveSessionsRepo()
    await repo.insert_pending(conn, id="abc", clip_id=42, prompt_version=None)
    r = await ac.post(
        "/api/live/sessions/abc/transcript",
        json={"end_reason": "nonsense", "transcript": []},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_transcript_unknown_session_404(client_and_db):
    ac, _ = client_and_db
    r = await ac.post(
        "/api/live/sessions/missing/transcript",
        json={"end_reason": "user_stop", "transcript": []},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
@respx.mock
async def test_summarize_route_happy_path(client_and_db):
    ac, conn = client_and_db
    repo = LiveSessionsRepo()
    await repo.insert_pending(conn, id="abc", clip_id=42, prompt_version=None)
    await repo.mark_active(conn, "abc")
    await repo.mark_ended(
        conn, "abc", end_reason="user_stop",
        transcript_json=json.dumps([
            {"role": "user", "text": "co je to za auto?", "ts": 1},
            {"role": "model", "text": "Škoda 30. léta.", "ts": 2},
        ], ensure_ascii=False),
    )
    respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-2.5-flash-lite:generateContent"
    ).mock(return_value=Response(200, json={
        "candidates": [{"content": {"parts": [{"text": "Škoda z 30. let na rodinném záběru."}]}}]
    }))
    r = await ac.post("/api/live/sessions/abc/summarize")
    assert r.status_code == 200, r.text
    assert r.json()["summary_cs"] == "Škoda z 30. let na rodinném záběru."
    assert (await repo.get(conn, "abc")).summary_cs == "Škoda z 30. let na rodinném záběru."


@pytest.mark.asyncio
@respx.mock
async def test_summarize_route_idempotent(client_and_db):
    ac, conn = client_and_db
    repo = LiveSessionsRepo()
    await repo.insert_pending(conn, id="abc", clip_id=42, prompt_version=None)
    await repo.mark_active(conn, "abc")
    await repo.mark_ended(
        conn, "abc", end_reason="user_stop",
        transcript_json=json.dumps([{"role": "user", "text": "x", "ts": 1}]),
    )
    await repo.set_summary(conn, "abc", "Existující.")
    r = await ac.post("/api/live/sessions/abc/summarize")
    assert r.status_code == 200
    assert r.json()["summary_cs"] == "Existující."
