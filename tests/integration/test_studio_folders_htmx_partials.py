"""T2-4: folder CRUD endpoints return HTMX partials when HX-Request: true,
JSON otherwise. The HTMX path replaces the studio.js `location.reload()`
pattern."""

import importlib

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


def test_create_folder_returns_partial_on_htmx_request(client):
    r = client.post(
        "/api/studio/folders",
        json={"name": "My folder"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code in (200, 201)
    # Partial is HTML, not JSON.
    assert r.headers["content-type"].startswith("text/html"), (
        f"HX-Request expected HTML; got {r.headers['content-type']}"
    )
    # Folder name appears in the rendered partial.
    assert "My folder" in r.text


def test_create_folder_returns_json_without_htmx_header(client):
    r = client.post("/api/studio/folders", json={"name": "JSON folder"})
    assert r.status_code in (200, 201)
    assert "application/json" in r.headers["content-type"]
    body = r.json()
    assert "id" in body


def test_add_clips_returns_partial_on_htmx_request(client):
    # Create folder first.
    r1 = client.post("/api/studio/folders", json={"name": "F"})
    folder_id = r1.json()["id"]

    r2 = client.post(
        f"/api/studio/folders/{folder_id}/clips",
        json={"clip_ids": [1]},
        headers={"HX-Request": "true"},
    )
    assert r2.status_code in (200, 201)
    assert r2.headers["content-type"].startswith("text/html")
