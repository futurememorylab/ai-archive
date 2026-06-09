"""Settings must resolve from OS env alone — the Cloud Run container has
no .env file; deploy/cloudrun.env.yaml + --set-secrets provide real env
vars. Guards against anyone making .env mandatory."""

import os

from backend.app.settings import Settings

_REQUIRED = {
    "CATDV_BASE_URL": "http://127.0.0.1:18080",
    "CATDV_CATALOG_ID": "881507",
    "GCP_PROJECT_ID": "catdav",
    "GCS_BUCKET_NAME": "catdav-proxies",
}


def test_settings_resolve_from_pure_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # no .env in cwd
    for key in list(os.environ):
        if key.startswith(("CATDV_", "GCP_", "GCS_", "APP_", "DATA_")):
            monkeypatch.delenv(key, raising=False)
    for key, value in _REQUIRED.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("DATA_DIR", "/data")

    s = Settings()

    assert s.app_env == "prod"
    assert s.catdv_base_url == "http://127.0.0.1:18080"
    assert s.catdv_catalog_id == 881507
    assert str(s.data_dir) == "/data"
    assert s.google_application_credentials is None  # ADC in cloud


def test_playback_source_defaults_local_overridable(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for key, value in _REQUIRED.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("PLAYBACK_SOURCE", raising=False)
    assert Settings().playback_source == "local"
    monkeypatch.setenv("PLAYBACK_SOURCE", "gcs")
    assert Settings().playback_source == "gcs"
