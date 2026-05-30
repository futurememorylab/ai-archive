"""The studio player slot lives in studio.html, NOT in
_studio_run_output.html — so re-fetching the run partial while a run
is in progress does not collapse the player. This test guards the
structural separation: the player slot's DOM marker must appear in
the studio.html render but NOT in the run partial.
"""

import asyncio
import importlib
import json

import aiosqlite
import pytest
from fastapi.testclient import TestClient


def _setenv(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("CATDV_PASSWORD", raising=False)


@pytest.fixture
def client(monkeypatch, tmp_path):
    _setenv(monkeypatch, tmp_path)
    from backend.app import main as main_mod
    importlib.reload(main_mod)
    with TestClient(main_mod.app) as c:
        yield c


def _make_prompt(client):
    r = client.post("/api/prompts", json={
        "name": "ps", "media_kind": "any", "model": "gemini-2.5-pro",
        "target_map": {}, "output_schema": {}, "body": "x",
    })
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    env = client.get(f"/api/prompts/{pid}").json()
    return pid, env["latest_version_id"]


def _seed_run(app, *, version_id, clip_id, status):
    db_path = app.state.core_ctx.settings.data_dir / "app.db"

    async def _go():
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "INSERT INTO studio_run(prompt_version_id, clip_id, status, "
                "output_json, model, finished_at) "
                "VALUES (?, ?, ?, ?, 'gemini-2.5-pro', '2026-05-28T00:00:00Z')",
                (version_id, clip_id, status,
                 json.dumps({"scenes": []}) if status == "ok" else None),
            )
            await db.commit()
    asyncio.run(_go())


def test_player_slot_in_page_not_in_run_partial(client):
    pid, vid = _make_prompt(client)
    page = client.get(f"/studio?prompt_id={pid}")
    assert page.status_code == 200
    assert "data-studio-player-slot" in page.text

    from backend.app import main as main_mod
    _seed_run(main_mod.app, version_id=vid, clip_id=12041, status="pending")
    partial = client.get(f"/studio/_run?prompt_version_id={vid}&clip_id=12041")
    assert partial.status_code == 200
    assert "data-studio-player-slot" not in partial.text, (
        "Run partial must not redefine the player slot — that would "
        "remount the player on every Output tab refresh."
    )
