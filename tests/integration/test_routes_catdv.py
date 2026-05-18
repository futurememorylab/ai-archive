import importlib

from fastapi.testclient import TestClient


def test_list_clips_proxies_to_catdv(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    from backend.app import main as main_mod
    importlib.reload(main_mod)
    app = main_mod.app

    with TestClient(app) as client:
        ctx = client.app.state.ctx
        async def _aexit(self, exc_type, exc, tb): pass
        ctx.catdv = type("FakeC", (), {"__aexit__": _aexit})()
        async def list_clips(*, catalog_id, offset=0, limit=50, q=None):
            return {"total": 1, "clips": [{"ID": 1, "name": "x"}]}
        async def get_clip(clip_id):
            return {"ID": clip_id, "name": "x"}
        ctx.catdv.list_clips = list_clips
        ctx.catdv.get_clip = get_clip

        r = client.get("/api/catdv/clips?limit=10")
        assert r.status_code == 200
        assert r.json()["total"] == 1
        assert r.json()["clips"][0]["ID"] == 1

        r = client.get("/api/catdv/clips/1")
        assert r.status_code == 200
        assert r.json()["ID"] == 1
