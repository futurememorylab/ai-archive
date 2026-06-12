"""POST /api/vpn/retry forces an on-demand VPN health re-probe and returns the
chip partial with a toast. Managed-only (409 when unmanaged)."""

import importlib

from fastapi.testclient import TestClient

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


class FakeSupervisor:
    """Minimal vpn_supervisor stub: probe_now() flips healthy to the seeded value."""

    def __init__(self, healthy_after_probe):
        self._healthy = False
        self._after = healthy_after_probe
        self.probe_called = False

    def status(self):
        from backend.app.services.vpn_supervisor import VpnStatus
        return VpnStatus(managed=True, desired="on",
                         process_running=True, healthy=self._healthy)

    async def probe_now(self):
        self.probe_called = True
        self._healthy = self._after
        return self.status()


def test_retry_managed_reprobes_and_returns_chip(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        sup = FakeSupervisor(healthy_after_probe=True)
        install_live_ctx(client.app, vpn_supervisor=sup)
        r = client.post("/api/vpn/retry", headers={"HX-Request": "true"})
        assert r.status_code == 200
        assert sup.probe_called is True
        assert "HX-Trigger" in r.headers              # toast bridge
        assert "VPN" in r.text


def test_retry_still_unreachable_toasts(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        sup = FakeSupervisor(healthy_after_probe=False)
        install_live_ctx(client.app, vpn_supervisor=sup)
        r = client.post("/api/vpn/retry", headers={"HX-Request": "true"})
        assert r.status_code == 200
        assert sup.probe_called is True
        assert "HX-Trigger" in r.headers
