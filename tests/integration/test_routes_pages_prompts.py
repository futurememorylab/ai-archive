"""SSR smoke tests for /prompts."""

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _setenv(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("CATDV_PASSWORD", raising=False)


def _make_app(monkeypatch, tmp_path):
    _setenv(monkeypatch, tmp_path)
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    return main_mod.app


@pytest.fixture
def client(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as c:
        yield c


def _new_body(**over):
    base = {
        "name": "Test Prompt",
        "description": "test",
        "body": "p",
        "target_map": {"x": {"kind": "markers"}},
        "output_schema": {"type": "object"},
        "model": "m",
    }
    base.update(over)
    return base


def test_prompts_page_renders_empty(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        r = client.get("/prompts")
        assert r.status_code == 200
        assert "Prompts" in r.text
        # Empty state when no seed loaded — the SSR test bypasses lifespan seeding.
        assert "No prompts yet" in r.text or "page-body" in r.text


def test_prompts_page_lists_seeded_prompt(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        # Manually seed one prompt via the API to avoid relying on the seed loader.
        pid = client.post("/api/prompts", json=_new_body()).json()["id"]
        r = client.get("/prompts")
        assert r.status_code == 200
        assert "Test Prompt" in r.text
        r = client.get(f"/prompts/{pid}")
        assert r.status_code == 200
        assert "Test Prompt" in r.text


def test_rail_includes_prompts_link(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        r = client.get("/prompts")
        assert r.status_code == 200
        assert 'href="/prompts"' in r.text


def test_prompts_detail_renders_editor_textareas(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        body = {
            "name": "Editor Test",
            "description": "",
            "body": "Edit me",
            "target_map": {"x": {"kind": "markers"}},
            "output_schema": {"type": "object"},
            "model": "gemini-2.5-pro",
        }
        pid = client.post("/api/prompts", json=body).json()["id"]
        r = client.get(f"/prompts/{pid}")
        assert r.status_code == 200
        # Editor textareas present:
        assert 'x-model="draft.body"' in r.text or "x-model='draft.body'" in r.text
        assert (
            'x-model="draft.target_map_text"' in r.text
            or "x-model='draft.target_map_text'" in r.text
        )
        assert (
            'x-model="draft.output_schema_text"' in r.text
            or "x-model='draft.output_schema_text'" in r.text
        )
        # Alpine factory bootstrap:
        assert "promptEditor({" in r.text
        # The new draft prompt isn't read-only (state=draft → canEdit=true):
        assert "Edit me" in r.text


def test_action_new_version_creates_draft_and_redirects(client):
    pid = client.post(
        "/api/prompts",
        json={
            "name": "NV",
            "description": "",
            "body": "p",
            "target_map": {"x": {"kind": "markers"}},
            "output_schema": {"type": "object"},
            "model": "gemini-2.5-pro",
        },
    ).json()["id"]
    vid = client.get(f"/api/prompts/{pid}").json()["versions"][0]["id"]
    client.post(f"/api/prompts/{pid}/versions/{vid}:promote")
    r = client.post(f"/prompts/{pid}/_new_version", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith(f"/prompts/{pid}?version_id=")
    new_vid = int(r.headers["location"].split("version_id=")[1])
    new_v = client.get(f"/api/prompts/{pid}/versions/{new_vid}").json()
    assert new_v["state"] == "draft"
    assert new_v["version_num"] == 2


def test_action_promote_version_redirects_to_detail(client):
    pid = client.post(
        "/api/prompts",
        json={
            "name": "PR",
            "description": "",
            "body": "p",
            "target_map": {"x": {"kind": "markers"}},
            "output_schema": {"type": "object"},
            "model": "gemini-2.5-pro",
        },
    ).json()["id"]
    vid = client.get(f"/api/prompts/{pid}").json()["versions"][0]["id"]
    r = client.post(f"/prompts/{pid}/versions/{vid}/_promote", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/prompts/{pid}?version_id={vid}"
    assert client.get(f"/api/prompts/{pid}/versions/{vid}").json()["state"] == "production"


def test_action_duplicate_redirects_to_new_prompt(client):
    pid = client.post(
        "/api/prompts",
        json={
            "name": "DUP",
            "description": "",
            "body": "p",
            "target_map": {"x": {"kind": "markers"}},
            "output_schema": {"type": "object"},
            "model": "gemini-2.5-pro",
        },
    ).json()["id"]
    r = client.post(f"/prompts/{pid}/_duplicate", follow_redirects=False)
    assert r.status_code == 303
    new_loc = r.headers["location"]
    assert new_loc.startswith("/prompts/")
    new_pid = int(new_loc.split("/")[-1])
    new_p = client.get(f"/api/prompts/{new_pid}").json()
    assert new_p["name"] == "Copy of DUP"


def test_new_prompt_form_renders(client):
    r = client.get("/prompts/new")
    assert r.status_code == 200
    assert 'action="/prompts/_create"' in r.text
    assert "New prompt" in r.text


def test_new_prompt_post_creates_and_redirects(client):
    r = client.post(
        "/prompts/_create",
        data={
            "name": "Brand New",
            "description": "ssr-created",
            "body": "Hello",
            "target_map": '{"x": {"kind": "markers"}}',
            "output_schema": '{"type": "object"}',
            "model": "gemini-2.5-pro",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].startswith("/prompts/")
    pid = int(r.headers["location"].rsplit("/", 1)[1])
    detail = client.get(f"/api/prompts/{pid}").json()
    assert detail["name"] == "Brand New"
    assert detail["versions"][0]["state"] == "draft"


def test_new_prompt_post_invalid_json_returns_400_with_form(client):
    r = client.post(
        "/prompts/_create",
        data={
            "name": "X",
            "description": "",
            "body": "h",
            "target_map": "not json",
            "output_schema": "{}",
            "model": "gemini-2.5-pro",
        },
    )
    assert r.status_code == 400
    assert "invalid JSON" in r.text
    assert "X" in r.text  # name persists in the form


def test_action_archive_then_restore(client):
    pid = client.post(
        "/api/prompts",
        json={
            "name": "ARCH",
            "description": "",
            "body": "p",
            "target_map": {"x": {"kind": "markers"}},
            "output_schema": {"type": "object"},
            "model": "gemini-2.5-pro",
        },
    ).json()["id"]
    r = client.post(f"/prompts/{pid}/_archive", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/prompts"
    # Now archived — list view excludes it.
    names = [p["name"] for p in client.get("/api/prompts").json()]
    assert "ARCH" not in names
    # Restore
    r = client.post(f"/prompts/{pid}/_restore", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/prompts/{pid}"
    names_after = [p["name"] for p in client.get("/api/prompts").json()]
    assert "ARCH" in names_after


def test_new_prompt_post_invalid_target_map_shape_returns_400(client):
    r = client.post(
        "/prompts/_create",
        data={
            "name": "BAD2",
            "description": "",
            "body": "p",
            "target_map": '{"x": {"kind": "field"}}',  # missing required 'identifier'
            "output_schema": "{}",
            "model": "gemini-2.5-pro",
        },
    )
    assert r.status_code == 400
    assert "identifier" in r.text or "target_map" in r.text


def test_action_new_version_404_on_unknown_prompt(client):
    r = client.post("/prompts/99999/_new_version", follow_redirects=False)
    assert r.status_code == 404


def test_version_picker_uses_canonical_menu_module(client):
    pid = client.post("/api/prompts", json=_new_body()).json()["id"]
    html = client.get(f"/prompts/{pid}").text
    # Migrated onto the shared ui.menu macro (popover + .menu-item).
    assert 'class="menu-item' in html
    assert 'x-data="popover()"' in html
    # The bespoke version-menu vocabulary is gone.
    assert "version-menu" not in html
