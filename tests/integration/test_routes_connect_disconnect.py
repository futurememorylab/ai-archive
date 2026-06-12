# tests/integration/test_routes_connect_disconnect.py
"""connect → login() + online; disconnect → logout() + disconnected;
login failures map to status codes + an HX-Trigger toast, never a seat."""

import importlib

from fastapi.testclient import TestClient

from backend.app.services.catdv_client import CatdvBusyError
from backend.app.services.connection_monitor import ConnectionMonitor, ConnectionState
from tests._helpers.live_ctx import install_live_ctx


def _make_app(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    return main_mod.app


class FakeClient:
    def __init__(self, *, login_exc=None):
        self._logged_in = False
        self._login_exc = login_exc

    @property
    def logged_in(self):
        return self._logged_in

    async def login(self):
        if self._login_exc is not None:
            raise self._login_exc
        self._logged_in = True

    async def logout(self):
        self._logged_in = False


def _install(app, client):
    ctx = app.state.core_ctx

    class P:
        async def health(self):
            class H:
                ok = client.logged_in
                reachable = True
            return H()

    monitor = ConnectionMonitor(
        provider=P(), db_provider=lambda: ctx.db, interval_s=99999.0,
        event_bus=ctx.event_bus, manual=True, logged_in=lambda: client.logged_in,
        initial_state=ConnectionState.disconnected,
    )
    install_live_ctx(app, connection_monitor=monitor, catdv=client)
    return monitor


def test_connect_success_goes_online(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        c = FakeClient()
        _install(client.app, c)
        r = client.post("/api/connection/connect")
        assert r.status_code == 200
        assert c.logged_in is True
        assert client.get("/api/connection/state").json()["state"] == "online"


def test_connect_busy_maps_409_and_no_seat(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        c = FakeClient(login_exc=CatdvBusyError("max sessions"))
        _install(client.app, c)
        r = client.post("/api/connection/connect")
        assert r.status_code == 409
        assert c.logged_in is False
        assert "HX-Trigger" in r.headers  # toast bridge


def test_disconnect_frees_seat(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        c = FakeClient()
        c._logged_in = True
        _install(client.app, c)
        r = client.post("/api/connection/disconnect")
        assert r.status_code == 200
        assert c.logged_in is False
        assert client.get("/api/connection/state").json()["state"] == "disconnected"


def test_retry_targeting_pill_reprobes_and_returns_pill(monkeypatch, tmp_path):
    # From the Unreachable pill state the user must be able to re-probe: a
    # retry that targets the pill re-probes and returns the PILL partial (not
    # the chip), so a recovered tunnel flips to Disconnected (Connect enabled).
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        c = FakeClient()  # not logged in; probe P.health() → ok=False, reachable=True
        _install(client.app, c)
        r = client.post(
            "/api/connection/retry",
            headers={"HX-Request": "true", "HX-Target": "connection-pill"},
        )
        assert r.status_code == 200
        assert 'id="connection-pill"' in r.text  # pill partial, not the chip
        assert "/api/connection/connect" in r.text  # reprobed → Disconnected → Connect


def test_connect_targeting_chip_returns_chip(monkeypatch, tmp_path):
    # The topbar chip is the live control surface; when it targets the
    # connect endpoint it must get the chip partial back (not the pill).
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        c = FakeClient()
        _install(client.app, c)
        r = client.post(
            "/api/connection/connect",
            headers={"HX-Request": "true", "HX-Target": "connection-chip"},
        )
        assert r.status_code == 200
        # Inner partial (swaps into the stable container). After connect the
        # client is logged in → online → shows the Disconnect action.
        assert "/api/connection/disconnect" in r.text and "Disconnect" in r.text


def test_connect_success_emits_toast(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        c = FakeClient()
        _install(client.app, c)
        r = client.post("/api/connection/connect")
        assert r.status_code == 200
        assert "HX-Trigger" in r.headers
        assert "CatDV connected" in r.headers["HX-Trigger"]
        import json
        assert json.loads(r.headers["HX-Trigger"])["toast"]["level"] == "success"


def test_disconnect_success_emits_toast(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        c = FakeClient()
        c._logged_in = True
        _install(client.app, c)
        r = client.post("/api/connection/disconnect")
        assert r.status_code == 200
        assert "HX-Trigger" in r.headers
        assert "CatDV disconnected" in r.headers["HX-Trigger"]
        import json
        assert json.loads(r.headers["HX-Trigger"])["toast"]["level"] == "info"


def test_htmx_pill_response_includes_pending_count(monkeypatch, tmp_path):
    # The swapped-in pill must render "Sync now (N)" with a real count, not a
    # blank "Sync now ()" flash until the next poll.
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        c = FakeClient()
        _install(client.app, c)
        r = client.post("/api/connection/connect", headers={"HX-Request": "true"})
        assert r.status_code == 200
        assert "Sync now (0)" in r.text
        assert "Sync now ()" not in r.text
