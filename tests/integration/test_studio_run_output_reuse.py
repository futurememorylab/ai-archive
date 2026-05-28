"""The Output tab now renders via the shared _anno_panels.html partial,
and embeds the raw run JSON in a <script type="application/json"
data-run-json> block for client-side diffing."""

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


def _seed_run(app, *, version_id, clip_id, output_json):
    """Insert a studio_run row via a fresh aiosqlite connection.

    Uses asyncio.new_event_loop() (Py 3.13 safe — get_event_loop() raises
    on the main thread). Looks up the db path via ctx.settings.data_dir
    because ctx.db_path is not exposed.
    """
    db_path = app.state.ctx.settings.data_dir / "app.db"
    async def _go():
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "INSERT INTO studio_run(prompt_version_id, clip_id, status, "
                "output_json, model, finished_at) "
                "VALUES (?, ?, 'ok', ?, 'gemini-2.5-pro', '2026-05-27T00:00:00Z')",
                (version_id, clip_id, json.dumps(output_json)),
            )
            await db.commit()
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_go())
    finally:
        loop.close()


def _seed_run_with_items(
    app, *, version_id, clip_id, scenes=None, fields=None, notes=None,
):
    """Seed a studio_run + review_items rows.

    `scenes` is a list of dicts in REAL Gemini shape:
        {"name": "...", "in": {"secs": float}, "out": {"secs": float}}
    `fields` is a dict identifier→value (passed through as proposed_value).
    `notes` is a dict identifier→str.
    """
    db_path = app.state.ctx.settings.data_dir / "app.db"

    async def _go():
        async with aiosqlite.connect(db_path) as db:
            output_json = {}
            if scenes is not None:
                output_json["scenes"] = scenes
            if fields is not None:
                output_json.update(fields)
            if notes is not None:
                output_json.update(notes)

            cur = await db.execute(
                "INSERT INTO studio_run(prompt_version_id, clip_id, status, "
                "output_json, model, finished_at) VALUES "
                "(?, ?, 'ok', ?, 'gemini-2.5-pro', '2026-05-27T00:00:00Z')",
                (version_id, clip_id, json.dumps(output_json)),
            )
            run_id = cur.lastrowid

            for scene in (scenes or []):
                await db.execute(
                    "INSERT INTO review_items(studio_run_id, catdv_clip_id, "
                    "kind, proposed_value, decision) VALUES (?, ?, ?, ?, 'pending')",
                    (run_id, clip_id, "marker", json.dumps(scene)),
                )
            for ident, val in (fields or {}).items():
                await db.execute(
                    "INSERT INTO review_items(studio_run_id, catdv_clip_id, "
                    "kind, target_identifier, proposed_value, decision) "
                    "VALUES (?, ?, ?, ?, ?, 'pending')",
                    (run_id, clip_id, "field", ident, json.dumps(val)),
                )
            for ident, val in (notes or {}).items():
                await db.execute(
                    "INSERT INTO review_items(studio_run_id, catdv_clip_id, "
                    "kind, target_identifier, proposed_value, decision) "
                    "VALUES (?, ?, ?, ?, ?, 'pending')",
                    (run_id, clip_id, "note", ident, json.dumps(val)),
                )
            await db.commit()
            return run_id

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_go())
    finally:
        loop.close()


def _make_prompt_with_version(client, *, target_map: dict):
    """Create a prompt via POST then read its latest_version_id via GET."""
    r = client.post("/api/prompts", json={
        "name": "t", "media_kind": "any", "model": "gemini-2.5-pro",
        "target_map": target_map, "output_schema": {}, "body": "x",
    })
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    env = client.get(f"/api/prompts/{pid}").json()
    return pid, env["latest_version_id"]


def test_run_output_uses_anno_panels_and_has_run_json(client):
    pid, vid = _make_prompt_with_version(client, target_map={
        "scenes": {"kind": "markers"},
        "summary": {"kind": "field", "identifier": "pf.summary"},
    })
    from backend.app import main as main_mod
    _seed_run_with_items(
        main_mod.app, version_id=vid, clip_id=12041,
        scenes=[{"name": "scene-a", "in": {"secs": 1.0}, "out": {"secs": 2.0}}],
        fields={"pf.summary": "krátký"},
    )

    r = client.get(f"/studio/_run?prompt_version_id={vid}&clip_id=12041")
    assert r.status_code == 200
    html = r.text

    # Shared partial is in.
    assert 'class="anno-tabs"' in html
    assert 'class="anno-section"' in html
    # Bespoke markup is gone.
    assert "ro-scene" not in html
    assert "ro-field" not in html
    # History tab is hidden in studio context.
    assert "tab === 'history'" not in html
    # Raw JSON is embedded for OutputDiff.
    assert 'type="application/json"' in html
    assert 'data-run-json' in html
    # Marker article rendering works.
    assert "scene-a" in html
    # Field identifier was looked up via target_map.
    assert "pf.summary" in html


def test_run_output_empty_state_when_no_run(client):
    pid, vid = _make_prompt_with_version(client, target_map={})
    r = client.get(f"/studio/_run?prompt_version_id={vid}&clip_id=99999")
    assert r.status_code == 200
    assert "No run yet" in r.text
    assert "anno-tabs" not in r.text


def test_marker_articles_have_seek_handler(client):
    """Smoke: rendered marker @click attr is present (the seek wiring is
    JS-only — exercised at runtime via studioPromptCard.seek())."""
    pid, vid = _make_prompt_with_version(client, target_map={
        "scenes": {"kind": "markers"},
    })
    from backend.app import main as main_mod
    _seed_run_with_items(
        main_mod.app, version_id=vid, clip_id=12041,
        scenes=[{"name": "s", "in": {"secs": 5.0}, "out": {"secs": 6.0}}],
    )
    r = client.get(f"/studio/_run?prompt_version_id={vid}&clip_id=12041")
    assert r.status_code == 200
    # _anno_panels.html marker articles call seek(secs) — present in the rendered HTML.
    assert '@click="seek(' in r.text
