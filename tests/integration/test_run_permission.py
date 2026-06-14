# tests/integration/test_run_permission.py
"""Only Annotator+ may trigger AI runs (cost + Gemini key + scarce CatDV seat).
A Viewer reaching the app is still refused at the run endpoints (spec
2026-06-14-iap-roles-admin-console-design.md). Drives identity via a mutable
holder so one app instance can act as two users."""
import importlib
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.auth.models import CurrentUser


def _app(monkeypatch, tmp_path, holder):
    for k, v in {
        "APP_ENV": "dev", "AUTH_BACKEND": "iap", "IAP_AUDIENCE": "aud",
        "ADMIN_EMAILS": "boss@x.com", "CATDV_BASE_URL": "http://localhost:0",
        "CATDV_USERNAME": "", "CATDV_PASSWORD": "p", "CATDV_CATALOG_ID": "881507",
        "GCP_PROJECT_ID": "p", "GCS_BUCKET_NAME": "b", "PROXY_SOURCE": "rest",
        "DATA_DIR": str(tmp_path),
    }.items():
        monkeypatch.setenv(k, v)
    from backend.app import main as main_mod
    importlib.reload(main_mod)
    monkeypatch.setattr(main_mod, "resolve_user",
                        lambda req, s: CurrentUser(email=holder["email"]))
    return main_mod


def test_viewer_cannot_create_job(monkeypatch, tmp_path: Path):
    holder = {"email": "boss@x.com"}
    main_mod = _app(monkeypatch, tmp_path, holder)
    with TestClient(main_mod.app) as client:
        # boss (admin) invites a viewer
        r = client.post("/admin/users",
                        data={"email": "viewer@x.com", "role": "viewer", "display_name": ""})
        assert r.status_code in (200, 201)
        # become the viewer; the run endpoint must refuse
        holder["email"] = "viewer@x.com"
        r = client.post("/api/jobs", json={"prompt_version_id": 1, "clip_ids": [1]})
    assert r.status_code == 403


def test_admin_passes_run_gate(monkeypatch, tmp_path: Path):
    holder = {"email": "boss@x.com"}
    main_mod = _app(monkeypatch, tmp_path, holder)
    with TestClient(main_mod.app) as client:
        r = client.post("/api/jobs", json={"prompt_version_id": 1, "clip_ids": [1]})
    # admin has 'run' → passes the gate (may 4xx later for other reasons, but NOT 403)
    assert r.status_code != 403
