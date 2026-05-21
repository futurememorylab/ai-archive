"""REST routes for /api/prompts."""
import importlib
from pathlib import Path

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


def _new_body(**over):
    base = {
        "name": "P1",
        "description": "d",
        "body": "Identify scenes.",
        "target_map": {"scenes": {"kind": "markers"}},
        "output_schema": {"type": "object"},
        "model": "gemini-2.5-pro",
    }
    base.update(over)
    return base


def test_create_and_get(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        r = client.post("/api/prompts", json=_new_body())
        assert r.status_code == 201, r.text
        pid = r.json()["id"]
        r = client.get(f"/api/prompts/{pid}")
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "P1"
        assert len(body["versions"]) == 1
        assert body["versions"][0]["state"] == "draft"
        assert body["current_production_version_id"] is None
        assert body["latest_version_id"] == body["versions"][0]["id"]


def test_list_active_excludes_archived(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        a = client.post("/api/prompts", json=_new_body(name="A")).json()["id"]
        client.post("/api/prompts", json=_new_body(name="B"))
        r = client.post(f"/api/prompts/{a}:archive")
        assert r.status_code == 200
        # Active list includes "B" but not "A" (seed prompts may also appear)
        active_names = [p["name"] for p in client.get("/api/prompts").json()]
        assert "B" in active_names
        assert "A" not in active_names
        # Archived list includes "A" but not "B"
        archived_names = [p["name"] for p in client.get("/api/prompts?archived=1").json()]
        assert "A" in archived_names
        assert "B" not in archived_names


def test_patch_name_collision_returns_409(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        client.post("/api/prompts", json=_new_body(name="A"))
        bid = client.post("/api/prompts", json=_new_body(name="B")).json()["id"]
        r = client.patch(f"/api/prompts/{bid}", json={"name": "A"})
        assert r.status_code == 409


def test_promote_then_edit_returns_409(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        pid = client.post("/api/prompts", json=_new_body()).json()["id"]
        vid = client.get(f"/api/prompts/{pid}").json()["versions"][0]["id"]
        r = client.post(f"/api/prompts/{pid}/versions/{vid}:promote")
        assert r.status_code == 200
        r = client.put(
            f"/api/prompts/{pid}/versions/{vid}",
            json={"body": "x", "target_map": {}, "output_schema": {}, "model": "m"},
        )
        assert r.status_code == 409
        assert r.json()["error_code"] == "version_immutable"


def test_create_version_clones_production_into_new_draft(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        pid = client.post("/api/prompts", json=_new_body()).json()["id"]
        vid = client.get(f"/api/prompts/{pid}").json()["versions"][0]["id"]
        client.post(f"/api/prompts/{pid}/versions/{vid}:promote")
        r = client.post(f"/api/prompts/{pid}/versions", json={})
        assert r.status_code == 201
        new_vid = r.json()["id"]
        detail = client.get(f"/api/prompts/{pid}").json()
        new_version = next(v for v in detail["versions"] if v["id"] == new_vid)
        assert new_version["state"] == "draft"
        assert new_version["version_num"] == 2
        assert new_version["body"] == "Identify scenes."


def test_promote_auto_archives_previous_production(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        pid = client.post("/api/prompts", json=_new_body()).json()["id"]
        v1 = client.get(f"/api/prompts/{pid}").json()["versions"][0]["id"]
        client.post(f"/api/prompts/{pid}/versions/{v1}:promote")
        v2 = client.post(f"/api/prompts/{pid}/versions", json={}).json()["id"]
        client.post(f"/api/prompts/{pid}/versions/{v2}:promote")
        detail = client.get(f"/api/prompts/{pid}").json()
        states = {v["id"]: v["state"] for v in detail["versions"]}
        assert states[v1] == "archived"
        assert states[v2] == "production"


def test_duplicate(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        pid = client.post("/api/prompts", json=_new_body(name="P")).json()["id"]
        r = client.post(f"/api/prompts/{pid}:duplicate")
        assert r.status_code == 201
        new_pid = r.json()["id"]
        detail = client.get(f"/api/prompts/{new_pid}").json()
        assert detail["name"] == "Copy of P"
        assert detail["versions"][0]["state"] == "draft"


def test_export_returns_full_shape(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        pid = client.post("/api/prompts", json=_new_body(name="X")).json()["id"]
        vid = client.get(f"/api/prompts/{pid}").json()["versions"][0]["id"]
        r = client.get(f"/api/prompts/{pid}/versions/{vid}/export")
        assert r.status_code == 200
        body = r.json()
        assert body["prompt"]["name"] == "X"
        assert body["version"]["body"] == "Identify scenes."
        assert body["version"]["target_map"] == {"scenes": {"kind": "markers"}}


def test_404_unknown_ids(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/prompts/999").status_code == 404
        assert client.put(
            "/api/prompts/1/versions/999",
            json={"body": "x", "target_map": {}, "output_schema": {}, "model": "m"},
        ).status_code == 404
