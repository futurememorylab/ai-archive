import importlib

from fastapi.testclient import TestClient


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


def test_get_connection_state_returns_default_when_external_disabled(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        r = client.get("/api/connection/state")
    assert r.status_code == 200
    assert r.json()["state"] in {"online", "offline", "degraded", "syncing"}


def test_post_offline_then_online_toggles_via_manager(monkeypatch, tmp_path):
    # With init_external=False the monitor is None; the routes return the
    # static default. To exercise the toggle we install a real monitor
    # manually after app boot.
    from backend.app.services.connection_monitor import ConnectionMonitor
    from tests._helpers.live_ctx import install_live_ctx

    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.core_ctx

        class FakeProvider:
            async def health(self):
                return None

        install_live_ctx(
            client.app,
            connection_monitor=ConnectionMonitor(
                provider=FakeProvider(),
                db_provider=lambda: ctx.db,
                interval_s=99999.0,
                event_bus=ctx.event_bus,
            ),
        )

        r = client.post("/api/connection/offline")
        assert r.status_code == 200
        assert r.json()["state"] == "offline"

        r = client.get("/api/connection/state")
        assert r.json()["state"] == "offline"

        r = client.post("/api/connection/online")
        assert r.status_code == 200
        assert r.json()["state"] == "online"
