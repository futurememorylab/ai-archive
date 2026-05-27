"""GET /studio/_player builds the shared overlay with cur (and optionally cmp)
rows. PR2 task 2 covers one-row mode; task 10 will exercise compare_id=."""

import asyncio
import importlib
import json

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


def _seed_run(client, *, version_id, clip_id, scenes):
    """Insert a studio_run row directly via a fresh sqlite connection on
    the same on-disk DB. We open a separate connection (and our own event
    loop) so we don't have to share state with the running app's loop.
    """
    import aiosqlite

    from backend.app import main as main_mod

    db_path = main_mod.app.state.ctx.settings.data_dir / "app.db"

    async def _go():
        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute(
                "INSERT INTO studio_run(prompt_version_id, clip_id, status, "
                "output_json, model, finished_at) "
                "VALUES (?, ?, 'ok', ?, 'gemini-2.5-pro', '2026-05-27T00:00:00Z')",
                (version_id, clip_id, json.dumps({"scenes": scenes})),
            )
            await db.commit()

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_go())
    finally:
        loop.close()


def test_player_one_row_no_version(client):
    r = client.get("/studio/_player?clip_id=12041")
    assert r.status_code == 200
    # No version_id → no overlay rows, but the player wrapper still renders.
    assert "data-clip-player" in r.text
    assert 'class="transport"' not in r.text or 'class="ranges' not in r.text


def test_player_one_row_with_version(client):
    r = client.post(
        "/api/prompts",
        json={
            "name": "t",
            "media_kind": "any",
            "model": "gemini-2.5-pro",
            "target_map": {},
            "output_schema": {},
            "body": "x",
        },
    )
    assert r.status_code == 201
    pid = r.json()["id"]
    # Fetch latest_version_id from the prompt envelope
    r2 = client.get(f"/api/prompts/{pid}")
    assert r2.status_code == 200
    vid = r2.json()["latest_version_id"]

    _seed_run(
        client,
        version_id=vid,
        clip_id=12041,
        scenes=[
            {"in_secs": 1.0, "out_secs": 2.0, "name": "a"},
            {"in_secs": 3.0, "out_secs": 4.0, "name": "b"},
        ],
    )

    r = client.get(f"/studio/_player?clip_id=12041&version_id={vid}")
    assert r.status_code == 200
    # One ranges row in the overlay (cur only), with two range divs.
    assert r.text.count('class="ranges range-cur"') == 1
    assert r.text.count('class="range"') >= 2
    # Legend names the version.
    assert "legend-range-cur" in r.text
