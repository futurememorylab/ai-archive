"""The pill renders Connect when disconnected, Disconnect when online, and
disables Connect when unreachable."""

import importlib

from fastapi.testclient import TestClient

from backend.app.services.connection_monitor import ConnectionMonitor, ConnectionState
from tests._helpers.live_ctx import install_live_ctx


def _make_app(monkeypatch, tmp_path):
    for k, v in {
        "APP_ENV": "dev", "CATDV_BASE_URL": "http://localhost:0",
        "CATDV_USERNAME": "", "CATDV_PASSWORD": "p", "CATDV_CATALOG_ID": "881507",
        "GCP_PROJECT_ID": "p", "GCS_BUCKET_NAME": "b", "DATA_DIR": str(tmp_path),
    }.items():
        monkeypatch.setenv(k, v)
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    return main_mod.app


class _Monitor:
    is_forced = False
    _forced_offline = False

    def __init__(self, state):
        self._state = state

    def current_state(self):
        return self._state


def _pill(monkeypatch, tmp_path, state):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        install_live_ctx(client.app, connection_monitor=_Monitor(state))
        return client.get("/ui/connection-pill").text


def test_disconnected_shows_connect(monkeypatch, tmp_path):
    html = _pill(monkeypatch, tmp_path, ConnectionState.disconnected)
    assert "/api/connection/connect" in html
    assert "Connect" in html


def test_online_shows_disconnect(monkeypatch, tmp_path):
    html = _pill(monkeypatch, tmp_path, ConnectionState.online)
    assert "/api/connection/disconnect" in html
    assert "Disconnect" in html


def test_unreachable_disables_connect(monkeypatch, tmp_path):
    html = _pill(monkeypatch, tmp_path, ConnectionState.offline)
    assert "disabled" in html
    assert "Unreachable" in html
