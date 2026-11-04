"""Lab (Prompt Studio) changes for researchers: the "Prompt:" header label,
the "New draft" action, and the dots on versions that have results."""

import importlib
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("CATDV_PASSWORD", raising=False)
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    with TestClient(main_mod.app) as c:
        c.db_path = tmp_path / "app.db"
        yield c


def _make_prompt(client, name="lab"):
    r = client.post(
        "/api/prompts",
        json={
            "name": name,
            "media_kind": "any",
            "model": "gemini-2.5-pro",
            "target_map": {},
            "output_schema": {},
            "body": "describe the scenes",
        },
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    v1 = client.get(f"/api/prompts/{pid}").json()["latest_version_id"]
    assert client.post(f"/api/prompts/{pid}/versions/{v1}:promote").status_code == 200
    return pid, v1


def test_header_names_the_prompt_with_a_label(client):
    pid, _ = _make_prompt(client, name="Scene markers")
    html = client.get(f"/studio?prompt_id={pid}").text
    assert '<span class="hdr-label">Prompt:</span>' in html
    assert "Scene markers" in html
    # The header keeps the "has results" version list fresh.
    assert "refreshRunVersions()" in html


def test_new_draft_copies_the_version_and_returns_the_cur_card(client):
    pid, v1 = _make_prompt(client)
    r = client.post(f"/studio/_new_draft?from_version_id={v1}")
    assert r.status_code == 200, r.text
    versions = client.get(f"/api/prompts/{pid}").json()["versions"]
    new = next(v for v in versions if v["id"] != v1)
    assert new["state"] == "draft"
    assert new["version_num"] == 2
    full = client.get(f"/api/prompts/{pid}/versions/{new['id']}").json()
    assert full["body"] == "describe the scenes"
    # The response is the editable cur card for the new draft.
    assert 'data-side="cur"' in r.text
    assert f'data-version-id="{new["id"]}"' in r.text
    assert "<textarea" in r.text
    toast = json.loads(r.headers["HX-Trigger"])["toast"]
    assert toast == {"message": "New draft v2 made from v1.", "level": "success"}


def test_new_draft_unknown_version_is_404(client):
    assert client.post("/studio/_new_draft?from_version_id=9999").status_code == 404


def test_new_draft_button_only_on_the_cur_card(client):
    _, v1 = _make_prompt(client)
    cur = client.get(f"/studio/_prompt_card?side=cur&prompt_version_id={v1}").text
    cmp = client.get(f"/studio/_prompt_card?side=cmp&prompt_version_id={v1}").text
    assert "data-new-draft" in cur
    assert f'hx-post="/studio/_new_draft?from_version_id={v1}"' in cur
    assert "data-new-draft" not in cmp


def test_version_menu_has_result_dots(client):
    _, v1 = _make_prompt(client)
    html = client.get(f"/studio/_prompt_card?side=cur&prompt_version_id={v1}").text
    assert "data-run-dot" in html
    assert f"$store.studio.runVersionIds.includes({v1})" in html
    assert 'data-version-num="1"' in html
    assert "has results for this clip" in html


def test_run_versions_lists_versions_with_finished_runs(client):
    _, v1 = _make_prompt(client)
    assert client.get("/api/studio/clips/77/run-versions").json() == []
    r = client.post("/api/studio/runs", json={"prompt_version_id": v1, "clip_id": 77})
    run_id = r.json()["run_id"]
    # Still pending: no dot yet.
    assert client.get("/api/studio/clips/77/run-versions").json() == []
    with sqlite3.connect(client.db_path) as conn:
        conn.execute("UPDATE studio_run SET status = 'ok' WHERE id = ?", (run_id,))
    assert client.get("/api/studio/clips/77/run-versions").json() == [v1]
    assert client.get("/api/studio/clips/78/run-versions").json() == []
