"""Studio page renders and includes the expected scaffolding."""

import importlib

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


def test_studio_page_renders(client):
    r = client.get("/studio")
    assert r.status_code == 200
    html = r.text
    # Page-level scaffolding
    assert "studio-page" in html
    assert "studio-hdr" in html
    assert "studio-body" in html


def test_studio_rail_button_present(client):
    # /prompts is archive-free and always 200 in the test environment
    r = client.get("/prompts")
    assert r.status_code == 200
    assert 'href="/studio"' in r.text


def test_studio_page_with_prompt_id_renders(client):
    # Even with an unknown prompt_id, the page must render
    r = client.get("/studio?prompt_id=999")
    assert r.status_code == 200


def _make_prompt_two_versions(client, *, name: str):
    """Create a prompt with v1 promoted to production and v2 branched as draft."""
    r = client.post("/api/prompts", json={
        "name": name, "media_kind": "any", "model": "gemini-2.5-pro",
        "target_map": {}, "output_schema": {}, "body": "v1",
    })
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    v1 = client.get(f"/api/prompts/{pid}").json()["latest_version_id"]
    # Promote v1 → production (so a fresh draft can be branched).
    pr = client.post(f"/api/prompts/{pid}/versions/{v1}:promote")
    assert pr.status_code == 200, pr.text
    # Branch v2 (draft, inherits v1's body — body content is irrelevant for these tests).
    r = client.post(f"/api/prompts/{pid}/versions", json={"from_version_id": v1})
    assert r.status_code == 201, r.text
    v2 = r.json()["id"]
    return pid, v1, v2


def test_studio_page_respects_version_id_param(client):
    pid, v1, v2 = _make_prompt_two_versions(client, name="vp")

    # Without param: default = draft = v2.
    r = client.get(f"/studio?prompt_id={pid}")
    assert r.status_code == 200
    assert f'activeVersionId: {v2}' in r.text

    # With param: pick v1 explicitly.
    r = client.get(f"/studio?prompt_id={pid}&version_id={v1}")
    assert r.status_code == 200
    assert f'activeVersionId: {v1}' in r.text


def test_studio_page_respects_compare_version_id_param(client):
    pid, v1, v2 = _make_prompt_two_versions(client, name="vp2")
    r = client.get(f"/studio?prompt_id={pid}&version_id={v2}&compare_version_id={v1}")
    assert r.status_code == 200
    assert f'compareVersionId: {v1}' in r.text


def test_studio_page_ignores_compare_equal_to_cur(client):
    pid, v1, _ = _make_prompt_two_versions(client, name="vp3")
    # Comparing a version with itself is a no-op.
    r = client.get(f"/studio?prompt_id={pid}&version_id={v1}&compare_version_id={v1}")
    assert r.status_code == 200
    assert "compareVersionId: null" in r.text
