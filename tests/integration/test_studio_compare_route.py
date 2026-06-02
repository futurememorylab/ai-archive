"""GET /studio/_compare — aligned scene table partial."""

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


def _two_versions(client):
    r = client.post("/api/prompts", json={
        "name": "cmp", "media_kind": "any", "model": "gemini-2.5-pro",
        "target_map": {}, "output_schema": {}, "body": "v1",
    })
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    v1 = client.get(f"/api/prompts/{pid}").json()["latest_version_id"]
    client.post(f"/api/prompts/{pid}/versions/{v1}:promote")
    r = client.post(f"/api/prompts/{pid}/versions", json={"from_version_id": v1})
    v2 = r.json()["id"]
    return pid, v1, v2


def _seed_run_with_markers(client, version_id, clip_id, markers):
    """Insert a studio_run + marker review_items rows so panels render.

    Table name is review_items (plural) and FK column is studio_run_id,
    as confirmed from test_studio_run_output_reuse.py canonical seeder.
    """
    from backend.app import main as main_mod
    db_path = main_mod.app.state.core_ctx.settings.data_dir / "app.db"

    async def _seed():
        async with aiosqlite.connect(db_path) as db:
            cur = await db.execute(
                "INSERT INTO studio_run(prompt_version_id, clip_id, status, "
                "output_json, model, finished_at) VALUES (?, ?, 'ok', ?, "
                "'gemini-2.5-pro', '2026-05-27T00:00:00Z')",
                (version_id, clip_id, json.dumps({})),
            )
            run_id = cur.lastrowid
            for mk in markers:
                await db.execute(
                    "INSERT INTO review_items(studio_run_id, catdv_clip_id, kind, "
                    "proposed_value, decision) VALUES (?, ?, 'marker', ?, 'pending')",
                    (run_id, clip_id, json.dumps({
                        "name": mk["name"],
                        "in": {"secs": mk["in"], "frm": 0},
                        "out": {"secs": mk["out"], "frm": 0},
                    })),
                )
            await db.commit()
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_seed())
    finally:
        loop.close()


def test_empty_state_without_clip(client):
    _, v1, v2 = _two_versions(client)
    r = client.get(f"/studio/_compare?version_id={v2}&compare_id={v1}")
    assert r.status_code == 200
    assert "Click a clip" in r.text


def test_renders_aligned_scene_rows_with_status_and_diff(client):
    _, v1, v2 = _two_versions(client)
    _seed_run_with_markers(client, v1, 12041, [{"name": "Woman at bench", "in": 7, "out": 28}])
    _seed_run_with_markers(client, v2, 12041, [{"name": "Woman peeling potatoes", "in": 7, "out": 17}])
    r = client.get(f"/studio/_compare?version_id={v2}&compare_id={v1}&clip_id=12041")
    assert r.status_code == 200
    assert "studio-compare-table" in r.text
    assert "data-scene-key" in r.text
    assert "diff-ins" in r.text and "diff-del" in r.text
    assert "CHANGED" in r.text
    assert "aligned scene" in r.text


def test_added_scene_renders_no_scene_placeholder(client):
    _, v1, v2 = _two_versions(client)
    _seed_run_with_markers(client, v2, 12041, [{"name": "New shot", "in": 0, "out": 5}])
    r = client.get(f"/studio/_compare?version_id={v2}&compare_id={v1}&clip_id=12041")
    assert r.status_code == 200
    assert "no scene" in r.text
    assert "ADDED" in r.text


def test_404_on_missing_version(client):
    _, v1, v2 = _two_versions(client)
    r = client.get(f"/studio/_compare?version_id=99999&compare_id={v1}&clip_id=12041")
    assert r.status_code == 404
