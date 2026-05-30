"""The right-pane empty / error states all render inside the dedicated
.run-empty / .run-error shells. PR3 adds visible styling to those
shells; the markup contract here is the regression guard."""

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
        "name": "ee", "media_kind": "any", "model": "gemini-2.5-pro",
        "target_map": {}, "output_schema": {}, "body": "x",
    })
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    env = client.get(f"/api/prompts/{pid}").json()
    return pid, env["latest_version_id"]


def _seed_run(app, *, version_id, clip_id, status, output_json=None, error=None):
    db_path = app.state.core_ctx.settings.data_dir / "app.db"

    async def _go():
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "INSERT INTO studio_run(prompt_version_id, clip_id, status, "
                "output_json, error, model, finished_at) "
                "VALUES (?, ?, ?, ?, ?, 'gemini-2.5-pro', '2026-05-28T00:00:00Z')",
                (version_id, clip_id, status,
                 json.dumps(output_json) if output_json else None,
                 error),
            )
            await db.commit()
    asyncio.run(_go())


def test_no_version_renders_run_empty_shell(client):
    r = client.get("/studio/_run?prompt_version_id=9999&clip_id=12041")
    assert r.status_code == 200
    assert "run-empty" in r.text
    assert "Unknown version" in r.text


def test_no_run_renders_run_empty_shell(client):
    _, vid = _make_prompt(client)
    r = client.get(f"/studio/_run?prompt_version_id={vid}&clip_id=99999")
    assert r.status_code == 200
    assert "run-empty" in r.text
    assert "No run yet" in r.text


def test_pending_run_renders_run_empty_shell(client):
    _, vid = _make_prompt(client)
    from backend.app import main as main_mod
    _seed_run(main_mod.app, version_id=vid, clip_id=12041, status="pending")
    r = client.get(f"/studio/_run?prompt_version_id={vid}&clip_id=12041")
    assert r.status_code == 200
    assert "run-empty" in r.text
    assert "Running" in r.text


def test_error_run_renders_run_error_shell(client):
    _, vid = _make_prompt(client)
    from backend.app import main as main_mod
    _seed_run(main_mod.app, version_id=vid, clip_id=12041,
              status="error", error="Gemini API: 503 backend overloaded")
    r = client.get(f"/studio/_run?prompt_version_id={vid}&clip_id=12041")
    assert r.status_code == 200
    assert 'class="run-error"' in r.text
    assert 'class="run-error-h"' in r.text
    assert 'class="run-error-msg"' in r.text
    assert "Gemini API: 503 backend overloaded" in r.text


def test_error_run_message_is_selectable():
    """Error messages must use user-select: text (PR3 polish). The
    enclosing CSS rule for .run-error-msg includes user-select: text
    and word-break."""
    from pathlib import Path
    css = Path("backend/app/static/app.css").read_text()
    assert ".run-error-msg" in css, "missing .run-error-msg rule"
    rule_start = css.index(".run-error-msg")
    rule_end = css.index("}", rule_start)
    assert "user-select" in css[rule_start:rule_end]
    assert "word-break" in css[rule_start:rule_end]
