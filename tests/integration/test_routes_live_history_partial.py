import json
from pathlib import Path

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.main import app
from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.live_sessions import LiveSessionsRepo

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


@pytest.mark.asyncio
async def test_clip_live_history_partial_renders(tmp_path):
    db_path = tmp_path / "t.db"
    conn = await aiosqlite.connect(db_path)
    await apply_migrations(conn, MIGRATIONS)
    repo = LiveSessionsRepo()
    await repo.insert_pending(conn, id="abc", clip_id=42, prompt_version=None)
    await repo.mark_active(conn, "abc")
    await repo.mark_ended(
        conn, "abc", end_reason="user_stop",
        transcript_json=json.dumps([{"role": "user", "text": "ahoj", "ts": 1}]),
    )
    await repo.set_summary(conn, "abc", "Krátké shrnutí.")

    class _Ctx:
        db = conn
        mode = "online"
        settings = type("S", (), {})()
    app.state.ctx = _Ctx()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/clips/42/live-history")
        assert r.status_code == 200
        html = r.text
        assert "abc" in html or "Krátké shrnutí." in html
        assert "user_stop" in html
    await conn.close()
