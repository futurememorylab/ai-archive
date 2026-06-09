import os

import pytest


def _mk(monkeypatch):
    monkeypatch.setenv("CATDV_BASE_URL", "http://x")
    monkeypatch.setenv("CATDV_CATALOG_ID", "1")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    from backend.app.settings import Settings
    return Settings()


def test_upload_defaults(monkeypatch):
    s = _mk(monkeypatch)
    assert s.studio_upload_max_mb == 500
    assert "video/mp4" in s.studio_upload_allowed_mimes
    assert "video/webm" in s.studio_upload_allowed_mimes
