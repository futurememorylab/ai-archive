"""Manual-connect settings: default to manual (the seat-safe Cloud Run
model) and bound idle auto-disconnect. See the manual-connect spec."""

from backend.app.settings import Settings

_REQUIRED = {
    "CATDV_BASE_URL": "http://127.0.0.1:18080",
    "CATDV_CATALOG_ID": "881507",
    "GCP_PROJECT_ID": "catdav",
    "GCS_BUCKET_NAME": "catdav-proxies",
}


def _env(monkeypatch, tmp_path, **extra):
    monkeypatch.chdir(tmp_path)  # no .env in cwd
    for key, value in {**_REQUIRED, **extra}.items():
        monkeypatch.setenv(key, value)


def test_connect_mode_defaults_manual(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    monkeypatch.delenv("CATDV_CONNECT_MODE", raising=False)
    assert Settings().catdv_connect_mode == "manual"


def test_connect_mode_overridable_to_auto(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, CATDV_CONNECT_MODE="auto")
    assert Settings().catdv_connect_mode == "auto"


def test_idle_logout_defaults_900_overridable(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    monkeypatch.delenv("CATDV_IDLE_LOGOUT_S", raising=False)
    assert Settings().catdv_idle_logout_s == 900
    monkeypatch.setenv("CATDV_IDLE_LOGOUT_S", "60")
    assert Settings().catdv_idle_logout_s == 60
