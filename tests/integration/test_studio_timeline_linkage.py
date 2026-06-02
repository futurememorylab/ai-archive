"""Comparison timeline: scene labels, status classes, and shared keys."""

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
    pid = r.json()["id"]
    v1 = client.get(f"/api/prompts/{pid}").json()["latest_version_id"]
    client.post(f"/api/prompts/{pid}/versions/{v1}:promote")
    v2 = client.post(f"/api/prompts/{pid}/versions",
                     json={"from_version_id": v1}).json()["id"]
    return pid, v1, v2


def _seed(client, version_id, clip_id, markers):
    """studio_run + marker review_items. NOTE: the review items table is
    `review_items` (plural); studio-run FK column is `studio_run_id`. Confirm
    against tests/integration/test_studio_run_output_reuse.py if an INSERT errors."""
    from backend.app import main as main_mod
    db_path = main_mod.app.state.core_ctx.settings.data_dir / "app.db"

    async def _do():
        async with aiosqlite.connect(db_path) as db:
            cur = await db.execute(
                "INSERT INTO studio_run(prompt_version_id, clip_id, status, "
                "output_json, model, finished_at) VALUES (?, ?, 'ok', ?, "
                "'gemini-2.5-pro', '2026-05-27T00:00:00Z')",
                (version_id, clip_id, json.dumps({})),
            )
            rid = cur.lastrowid
            for mk in markers:
                await db.execute(
                    "INSERT INTO review_items(studio_run_id, catdv_clip_id, kind, "
                    "proposed_value, decision) VALUES (?, ?, 'marker', ?, 'pending')",
                    (rid, clip_id, json.dumps({
                        "name": mk["name"],
                        "in": {"secs": mk["in"], "frm": 0},
                        "out": {"secs": mk["out"], "frm": 0},
                    })),
                )
            await db.commit()
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_do())
    finally:
        loop.close()


def test_timeline_has_labels_status_and_scene_keys_when_comparing(client):
    _, v1, v2 = _two_versions(client)
    _seed(client, v1, 12041, [{"name": "Woman at bench", "in": 7, "out": 28}])
    _seed(client, v2, 12041, [{"name": "Woman peeling potatoes", "in": 7, "out": 17}])
    r = client.get(f"/studio/_player?clip_id=12041&version_id={v2}&compare_id={v1}")
    assert r.status_code == 200
    html = r.text
    assert "range-label" in html
    assert "data-scene-key" in html
    assert "range-st-" in html
    assert "Woman" in html


def test_scene_keys_match_table(client):
    _, v1, v2 = _two_versions(client)
    _seed(client, v1, 12041, [{"name": "A", "in": 7, "out": 28}])
    _seed(client, v2, 12041, [{"name": "B", "in": 7, "out": 17}])
    tl = client.get(f"/studio/_player?clip_id=12041&version_id={v2}&compare_id={v1}").text
    tbl = client.get(f"/studio/_compare?version_id={v2}&compare_id={v1}&clip_id=12041").text
    assert 'data-scene-key="scene-0"' in tl
    assert 'data-scene-key="scene-0"' in tbl
