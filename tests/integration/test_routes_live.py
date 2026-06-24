import json
from pathlib import Path

import aiosqlite
import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

from backend.app.main import app
from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.live_sessions import LiveSessionsRepo
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
        conn,
        seed_path=SEEDS / "live_system_instruction_cs.json",
    )

    class _Ctx:
        db = conn
        mode = "online"
        settings = type(
            "S",
            (),
            {
                "gemini_api_key": "test-key",
                "gemini_live_model": "gemini-2.5-flash-native-audio-latest",
                "gemini_live_voice": "Aoede",
                "gemini_live_inactivity_s": 60,
                "gemini_model": "gemini-2.5-flash-lite",
                # Read by the _attach_current_user middleware on every request.
                "auth_backend": "dev",
                "dev_user_email": "dev@localhost",
            },
        )()

    _ctx = _Ctx()
    app.state.core_ctx = _ctx
    app.state.live_ctx = _ctx
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
            id=clip_id,
            name="P1010001",
            format="9,5 mm",
            fps=25,
            duration_secs=120.0,
            duration_smpte="00:02:00:00",
            notes="rodinný výlet",
            big_notes="",
            markers=[],
            fields={},
        )

    async def fake_load_draft(ctx, clip_id):
        return dict(markers=[], fields={}, notes="")

    monkeypatch.setattr(live_routes, "load_clip_for_live", fake_load_clip)
    monkeypatch.setattr(live_routes, "load_draft_for_live", fake_load_draft)

    # Live now mints a short-lived, config-bound ephemeral token server-side.
    # The raw GEMINI_API_KEY authenticates THIS mint call (server→Google) and
    # never reaches the browser. See ADR 0112 (supersedes 0043).
    mint = respx.post(
        "https://generativelanguage.googleapis.com/v1alpha/auth_tokens"
    ).mock(return_value=Response(200, json={"name": "auth_tokens/ephemeral-XYZ"}))

    r = await ac.get("/api/live/session-config", params={"clip_id": 42})
    assert r.status_code == 200, r.text
    data = r.json()
    assert mint.called
    assert data["session_id"]
    assert data["ws_url"].startswith("wss://generativelanguage.googleapis.com/ws/")
    # Browser authenticates with the ephemeral token via ?access_token=, against
    # the v1alpha Constrained endpoint — NOT the raw key, NOT ?key=.
    assert "access_token=auth_tokens/ephemeral-XYZ" in data["ws_url"]
    assert "v1alpha.GenerativeService.BidiGenerateContentConstrained" in data["ws_url"]
    assert "key=test-key" not in data["ws_url"]
    assert "test-key" not in r.text  # the real key never appears in the response
    assert data["setup_payload"]["model"].endswith("native-audio-latest")
    # Hardening: the proprietary system prompt + tool/function declarations are
    # bound into the token server-side and must NOT be shipped to the browser.
    assert "systemInstruction" not in data["setup_payload"]
    assert "tools" not in data["setup_payload"]
    # The API key is not duplicated as a bare `token`.
    assert "token" not in data
    # inactivity_s is a server-rendered template arg, not part of this response.
    assert "inactivity_s" not in data
    # The initial context turn is a separate top-level field, NOT smuggled
    # inside setup_payload (which must be a pure BidiGenerateContentSetup so
    # the browser can send it verbatim as the `setup` frame).
    assert "initial_context_turn" not in data["setup_payload"]
    assert data["initial_context_turn"]["parts"][0]["text"].startswith(
        "=== Publikované anotace"
    )
    repo = LiveSessionsRepo()
    row = await repo.get(conn, data["session_id"])
    assert row.state == "pending"


@pytest.mark.asyncio
@respx.mock
async def test_session_config_works_offline_when_clip_cached(client_and_db, monkeypatch):
    """Mirrors annotate-button visibility: when CatDV is offline but the
    clip's proxy is cached locally, the Live route still works. Audio is
    browser↔Google direct (Gemini Developer API, not VPN-dependent) and the
    clip view-model is served from clip_cache by the offline-fallback path.
    """
    ac, conn = client_and_db
    app.state.core_ctx.mode = "offline"

    import backend.app.routes.live as live_routes

    async def fake_load_clip(ctx, clip_id):
        return dict(
            id=clip_id,
            name="P1010001",
            format="9,5 mm",
            fps=25,
            duration_secs=120.0,
            duration_smpte="00:02:00:00",
            notes="rodinný výlet",
            big_notes="",
            markers=[],
            fields={},
        )

    async def fake_load_draft(ctx, clip_id):
        return dict(markers=[], fields={}, notes="")

    monkeypatch.setattr(live_routes, "load_clip_for_live", fake_load_clip)
    monkeypatch.setattr(live_routes, "load_draft_for_live", fake_load_draft)

    # Minting the ephemeral token is a Google Developer API call (browser↔Google
    # direct, not VPN-dependent), so Live works even while CatDV is offline.
    respx.post(
        "https://generativelanguage.googleapis.com/v1alpha/auth_tokens"
    ).mock(return_value=Response(200, json={"name": "auth_tokens/ephemeral-OFF"}))

    r = await ac.get("/api/live/session-config", params={"clip_id": 42})
    assert r.status_code == 200, r.text
    ws_url = r.json()["ws_url"]
    assert "access_token=auth_tokens/ephemeral-OFF" in ws_url
    assert "test-key" not in ws_url


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
        conn,
        "abc",
        end_reason="user_stop",
        transcript_json=json.dumps(
            [
                {"role": "user", "text": "co je to za auto?", "ts": 1},
                {"role": "model", "text": "Škoda 30. léta.", "ts": 2},
            ],
            ensure_ascii=False,
        ),
    )
    respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-2.5-flash-lite:generateContent"
    ).mock(
        return_value=Response(
            200,
            json={
                "candidates": [
                    {"content": {"parts": [{"text": "Škoda z 30. let na rodinném záběru."}]}}
                ]
            },
        )
    )
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
        conn,
        "abc",
        end_reason="user_stop",
        transcript_json=json.dumps([{"role": "user", "text": "x", "ts": 1}]),
    )
    await repo.set_summary(conn, "abc", "Existující.")
    r = await ac.post("/api/live/sessions/abc/summarize")
    assert r.status_code == 200
    assert r.json()["summary_cs"] == "Existující."


@pytest.mark.asyncio
async def test_list_by_clip(client_and_db):
    ac, conn = client_and_db
    repo = LiveSessionsRepo()
    await repo.insert_pending(conn, id="a", clip_id=42, prompt_version=None)
    await repo.mark_active(conn, "a")
    await repo.mark_ended(
        conn,
        "a",
        end_reason="user_stop",
        transcript_json=json.dumps([{"role": "u", "text": "x", "ts": 1}]),
    )
    await repo.insert_pending(conn, id="b", clip_id=99, prompt_version=None)
    r = await ac.get("/api/live/sessions", params={"clip_id": 42})
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["id"] == "a"
    assert data[0]["end_reason"] == "user_stop"
    assert "has_summary" in data[0]
    assert data[0]["has_summary"] is False


@pytest.mark.asyncio
async def test_get_detail(client_and_db):
    ac, conn = client_and_db
    repo = LiveSessionsRepo()
    await repo.insert_pending(conn, id="abc", clip_id=42, prompt_version=None)
    await repo.mark_active(conn, "abc")
    await repo.mark_ended(
        conn,
        "abc",
        end_reason="user_stop",
        transcript_json=json.dumps([{"role": "u", "text": "hi", "ts": 1}]),
    )
    await repo.set_summary(conn, "abc", "Shrnutí.")
    r = await ac.get("/api/live/sessions/abc")
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == "abc"
    assert data["summary_cs"] == "Shrnutí."
    assert data["transcript"] == [{"role": "u", "text": "hi", "ts": 1}]


@pytest.mark.asyncio
async def test_get_detail_404(client_and_db):
    ac, _ = client_and_db
    r = await ac.get("/api/live/sessions/no-such")
    assert r.status_code == 404
