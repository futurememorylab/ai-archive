"""INSTANCE_ID is mandatory and slug-validated. It namespaces uploaded-clip
GCS keys so two app instances sharing one bucket cannot overwrite each
other's media (issue #55)."""

import os

import pytest
from pydantic import ValidationError

from backend.app.settings import Settings

_REQUIRED = {
    "CATDV_BASE_URL": "http://127.0.0.1:18080",
    "CATDV_CATALOG_ID": "881507",
    "GCP_PROJECT_ID": "catdav",
    "GCS_BUCKET_NAME": "catdav-proxies",
}


def _clean_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # no .env in cwd
    for key in list(os.environ):
        if key.startswith(("CATDV_", "GCP_", "GCS_", "APP_", "DATA_", "GOOGLE_", "INSTANCE_")):
            monkeypatch.delenv(key, raising=False)
    for key, value in _REQUIRED.items():
        monkeypatch.setenv(key, value)


def test_instance_id_required(monkeypatch, tmp_path):
    _clean_env(monkeypatch, tmp_path)
    # INSTANCE_ID deliberately unset
    with pytest.raises(ValidationError):
        Settings()


def test_instance_id_accepts_slug(monkeypatch, tmp_path):
    _clean_env(monkeypatch, tmp_path)
    monkeypatch.setenv("INSTANCE_ID", "local-pete")
    assert Settings().instance_id == "local-pete"


@pytest.mark.parametrize("bad", ["", "Has Space", "UPPER", "under_score", "-leading"])
def test_instance_id_rejects_non_slug(monkeypatch, tmp_path, bad):
    _clean_env(monkeypatch, tmp_path)
    monkeypatch.setenv("INSTANCE_ID", bad)
    with pytest.raises(ValidationError):
        Settings()
