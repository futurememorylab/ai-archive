"""Integration tests for /api/studio routes."""

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


def test_create_list_rename_delete_folder(client):
    r = client.post("/api/studio/folders", json={"name": "edge_cases"})
    assert r.status_code == 201
    fid = r.json()["id"]

    r = client.get("/api/studio/folders")
    assert r.status_code == 200
    folders = r.json()
    assert len(folders) == 1
    assert folders[0]["name"] == "edge_cases"
    assert folders[0]["clip_count"] == 0

    r = client.patch(f"/api/studio/folders/{fid}", json={"name": "rare"})
    assert r.status_code == 200
    r = client.get("/api/studio/folders")
    assert r.json()[0]["name"] == "rare"

    r = client.delete(f"/api/studio/folders/{fid}")
    assert r.status_code == 204
    r = client.get("/api/studio/folders")
    assert r.json() == []


def test_duplicate_folder_name_rejected(client):
    client.post("/api/studio/folders", json={"name": "x"})
    r = client.post("/api/studio/folders", json={"name": "x"})
    assert r.status_code == 409


def test_add_list_remove_clips(client):
    r = client.post("/api/studio/folders", json={"name": "f"})
    fid = r.json()["id"]

    r = client.post(f"/api/studio/folders/{fid}/clips", json={"clip_ids": [12041, 12042]})
    assert r.status_code == 200
    assert r.json()["added"] == 2

    r = client.get(f"/api/studio/folders/{fid}/clips")
    assert r.status_code == 200
    clips = r.json()
    assert sorted(c["clip_id"] for c in clips) == [12041, 12042]

    r = client.delete(f"/api/studio/folders/{fid}/clips/12041")
    assert r.status_code == 204

    r = client.get(f"/api/studio/folders/{fid}/clips")
    assert [c["clip_id"] for c in r.json()] == [12042]
