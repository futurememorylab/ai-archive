"""Routes for workspace CRUD + prep + release.

These tests boot the FastAPI app with `init_external=False`, then
manually wire a WorkspaceManager with a fake provider/resolver so the
HTTP shape is exercised end-to-end without needing CatDV.
"""

import asyncio
import importlib
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.archive.provider import ProviderCapabilities
from backend.app.repositories.workspaces import WorkspacesRepo
from backend.app.services.workspace_manager import WorkspaceManager


def _setenv(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))


def _make_app(monkeypatch, tmp_path):
    _setenv(monkeypatch, tmp_path)
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    return main_mod.app


class _FakeProvider:
    id = "catdv"
    capabilities = ProviderCapabilities(
        supports_markers=True,
        supports_notes=frozenset({"notes"}),
        supports_field_create=False,
        supports_etag=False,
        media_is_local=True,  # skip resolver in routes tests
        write_atomicity="per-clip",
    )

    async def get_clip(self, clip_id: str):
        return None


def _attach_manager(ctx):
    ctx.workspace_manager = WorkspaceManager(
        workspaces_repo=WorkspacesRepo(),
        provider=_FakeProvider(),
        proxy_resolver=None,
        db_provider=lambda c=ctx: c.db,
    )


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_create_and_list_workspace(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        _attach_manager(client.app.state.ctx)
        r = client.post(
            "/api/workspaces",
            json={
                "name": "test",
                "provider_id": "catdv",
                "catalog_id": "881507",
                "clip_keys": [["catdv", "1"], ["catdv", "2"]],
            },
        )
        assert r.status_code == 201, r.text
        ws_id = r.json()["id"]

        r = client.get("/api/workspaces")
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) == 1
        assert rows[0]["name"] == "test"

        r = client.get(f"/api/workspaces/{ws_id}")
        body = r.json()
        assert body["name"] == "test"
        assert len(body["clips"]) == 2


def test_add_remove_clip_routes(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        _attach_manager(client.app.state.ctx)
        r = client.post(
            "/api/workspaces",
            json={"name": "w", "catalog_id": "1", "clip_keys": []},
        )
        ws_id = r.json()["id"]

        r = client.post(
            f"/api/workspaces/{ws_id}/clips",
            json={"clip_keys": [["catdv", "5"]]},
        )
        assert r.status_code == 200
        r = client.get(f"/api/workspaces/{ws_id}")
        assert len(r.json()["clips"]) == 1

        r = client.delete(f"/api/workspaces/{ws_id}/clips/catdv/5")
        assert r.status_code == 200
        r = client.get(f"/api/workspaces/{ws_id}")
        assert r.json()["clips"] == []


def test_prepare_streams_events(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        _attach_manager(client.app.state.ctx)
        r = client.post(
            "/api/workspaces",
            json={
                "name": "w",
                "catalog_id": "1",
                "clip_keys": [["catdv", "1"]],
            },
        )
        ws_id = r.json()["id"]
        with client.stream("POST", f"/api/workspaces/{ws_id}/prepare") as resp:
            assert resp.status_code == 200
            body = b"".join(resp.iter_bytes())
        # Expect at least metadata + ready events
        text = body.decode()
        assert "metadata" in text
        assert "ready" in text


def test_release_does_not_delete_workspace_by_default(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        _attach_manager(client.app.state.ctx)
        r = client.post(
            "/api/workspaces",
            json={"name": "w", "catalog_id": "1", "clip_keys": [["catdv", "1"]]},
        )
        ws_id = r.json()["id"]
        r = client.post(f"/api/workspaces/{ws_id}/release")
        assert r.status_code == 200
        r = client.get(f"/api/workspaces/{ws_id}")
        assert r.status_code == 200
        assert r.json()["clips"] == []


def test_release_with_delete_removes_workspace(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        _attach_manager(client.app.state.ctx)
        r = client.post(
            "/api/workspaces",
            json={"name": "w", "catalog_id": "1", "clip_keys": []},
        )
        ws_id = r.json()["id"]
        r = client.post(f"/api/workspaces/{ws_id}/release?delete=true")
        assert r.status_code == 200
        r = client.get(f"/api/workspaces/{ws_id}")
        assert r.status_code == 404
