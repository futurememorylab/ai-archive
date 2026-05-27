"""GET /studio/_prompt_card — side-aware partial used by HTMX swaps."""

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


def _make_prompt_two_versions(client):
    """Create a prompt with v1 promoted to production and v2 branched as draft."""
    r = client.post("/api/prompts", json={
        "name": "pc", "media_kind": "any", "model": "gemini-2.5-pro",
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


def test_404_on_missing_version(client):
    r = client.get("/studio/_prompt_card?side=cur&prompt_version_id=9999")
    assert r.status_code == 404


def test_422_on_invalid_side(client):
    _, v1, _ = _make_prompt_two_versions(client)
    r = client.get(f"/studio/_prompt_card?side=garbage&prompt_version_id={v1}")
    assert r.status_code == 422  # FastAPI Literal validation


def test_draft_renders_textarea(client):
    _, _, v2 = _make_prompt_two_versions(client)
    r = client.get(f"/studio/_prompt_card?side=cur&prompt_version_id={v2}")
    assert r.status_code == 200
    assert "<textarea" in r.text
    assert "pc-readonly" not in r.text


def test_non_draft_renders_readonly_pre(client):
    _, v1, _ = _make_prompt_two_versions(client)
    r = client.get(f"/studio/_prompt_card?side=cur&prompt_version_id={v1}")
    assert r.status_code == 200
    assert 'class="pc-readonly mono"' in r.text
    assert "<textarea" not in r.text


def test_includes_data_attrs_for_alpine_sync(client):
    _, v1, _ = _make_prompt_two_versions(client)
    r = client.get(f"/studio/_prompt_card?side=cur&prompt_version_id={v1}")
    assert 'data-side="cur"' in r.text
    assert f'data-version-id="{v1}"' in r.text


def test_cmp_side_renders_close_and_diff_toggle(client):
    _, v1, _ = _make_prompt_two_versions(client)
    r = client.get(f"/studio/_prompt_card?side=cmp&prompt_version_id={v1}")
    assert r.status_code == 200
    assert 'data-side="cmp"' in r.text
    assert 'btn-close-cmp' in r.text
    assert 'btn-diff-toggle' in r.text


def test_output_tab_includes_data_run_json_when_run_exists(client):
    from backend.app import main as main_mod

    _, v1, _ = _make_prompt_two_versions(client)
    db_path = main_mod.app.state.ctx.settings.data_dir / "app.db"

    async def _seed():
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "INSERT INTO studio_run(prompt_version_id, clip_id, status, "
                "output_json, model, finished_at) "
                "VALUES (?, ?, 'ok', ?, 'gemini-2.5-pro', '2026-05-27T00:00:00Z')",
                (v1, 12041, json.dumps({"scenes": []})),
            )
            await db.commit()
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_seed())
    finally:
        loop.close()

    r = client.get(f"/studio/_prompt_card?side=cur&prompt_version_id={v1}&clip_id=12041")
    assert r.status_code == 200
    assert "data-run-json" in r.text


def test_prompt_card_lists_all_versions_in_picker(client):
    _, v1, v2 = _make_prompt_two_versions(client)
    r = client.get(f"/studio/_prompt_card?side=cur&prompt_version_id={v2}")
    assert r.status_code == 200
    # Both versions show up in the dropdown.
    assert f'data-version-pick="{v1}"' in r.text
    assert f'data-version-pick="{v2}"' in r.text
    # Active version is marked.
    assert 'is-current' in r.text


def test_picker_uses_hx_get_to_swap_card(client):
    _, v1, v2 = _make_prompt_two_versions(client)
    r = client.get(f"/studio/_prompt_card?side=cur&prompt_version_id={v2}")
    html = r.text
    assert 'hx-get="/studio/_prompt_card' in html
    assert 'hx-target="closest .studio-prompt-card"' in html
    assert 'hx-swap="outerHTML"' in html
