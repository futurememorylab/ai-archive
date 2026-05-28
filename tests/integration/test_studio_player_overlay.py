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
    """Insert a studio_run + review_items rows. Accepts either the legacy
    flat-shape scenes ({in_secs, out_secs}) or the nested-secs shape, and
    normalizes to nested before persisting — the overlay reads from
    review_items (kind='marker') which always carry the nested shape.
    """
    import aiosqlite

    from backend.app import main as main_mod

    db_path = main_mod.app.state.ctx.settings.data_dir / "app.db"

    def _to_nested(s: dict) -> dict:
        if "in" in s and isinstance(s["in"], dict):
            return s
        nested = {"name": s.get("name", "")}
        if "in_secs" in s:
            nested["in"] = {"secs": float(s["in_secs"])}
        if "out_secs" in s and s["out_secs"] is not None:
            nested["out"] = {"secs": float(s["out_secs"])}
        return nested

    normalized = [_to_nested(s) for s in scenes]

    async def _go():
        async with aiosqlite.connect(str(db_path)) as db:
            cur = await db.execute(
                "INSERT INTO studio_run(prompt_version_id, clip_id, status, "
                "output_json, model, finished_at) "
                "VALUES (?, ?, 'ok', ?, 'gemini-2.5-pro', '2026-05-27T00:00:00Z')",
                (version_id, clip_id, json.dumps({"scenes": normalized})),
            )
            run_id = cur.lastrowid
            for scene in normalized:
                await db.execute(
                    "INSERT INTO review_items(studio_run_id, catdv_clip_id, "
                    "kind, proposed_value, decision) VALUES (?, ?, ?, ?, 'pending')",
                    (run_id, clip_id, "marker", json.dumps(scene)),
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
    # transport wrapper is unconditional; the meaningful check is that
    # no ranges row rendered for the no-version case.
    assert 'class="ranges' not in r.text


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


def _two_versions(client):
    """Create a prompt with v1 promoted and v2 branched."""
    r = client.post("/api/prompts", json={
        "name": "p2", "media_kind": "any", "model": "gemini-2.5-pro",
        "target_map": {}, "output_schema": {}, "body": "v1",
    })
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    v1 = client.get(f"/api/prompts/{pid}").json()["latest_version_id"]
    pr = client.post(f"/api/prompts/{pid}/versions/{v1}:promote")
    assert pr.status_code == 200, pr.text
    r = client.post(f"/api/prompts/{pid}/versions", json={"from_version_id": v1})
    assert r.status_code == 201, r.text
    v2 = r.json()["id"]
    return pid, v1, v2


def test_player_two_rows_with_compare_id(client):
    pid, v1, v2 = _two_versions(client)
    _seed_run(client, version_id=v1, clip_id=12041, scenes=[
        {"in_secs": 1.0, "out_secs": 2.0, "name": "v1-scene"},
    ])
    _seed_run(client, version_id=v2, clip_id=12041, scenes=[
        {"in_secs": 5.0, "out_secs": 6.0, "name": "v2-scene-a"},
        {"in_secs": 7.0, "out_secs": 8.0, "name": "v2-scene-b"},
    ])

    r = client.get(f"/studio/_player?clip_id=12041&version_id={v2}&compare_id={v1}")
    assert r.status_code == 200
    # Two ranges rows.
    assert 'class="ranges range-cur"' in r.text
    assert 'class="ranges range-cmp"' in r.text
    # Legend names both versions (both have non-empty scenes).
    assert 'legend-range-cur' in r.text
    assert 'legend-range-cmp' in r.text


def test_player_two_rows_with_empty_cmp(client):
    """compare_id given but no run exists for it → row renders but with 0 scenes."""
    pid, v1, v2 = _two_versions(client)
    _seed_run(client, version_id=v1, clip_id=12041, scenes=[
        {"in_secs": 1.0, "out_secs": 2.0, "name": "x"},
    ])
    # No run on v2.
    r = client.get(f"/studio/_player?clip_id=12041&version_id={v1}&compare_id={v2}")
    assert r.status_code == 200
    assert 'class="ranges range-cur"' in r.text
    # range-cmp row container is still emitted (empty ranges list).
    assert 'class="ranges range-cmp"' in r.text
    # Legend should NOT include the empty row (selectattr filter in template).
    assert 'legend-range-cmp' not in r.text
