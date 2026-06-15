"""The denial page lets a reached-but-unroled user record an access request
(in-console, no email promised) and shows who they're signed in as."""
import importlib
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.auth.models import CurrentUser


def _app(monkeypatch, tmp_path, email):
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
                        lambda req, s: CurrentUser(email=email))
    return main_mod


def test_request_access_records_pending(monkeypatch, tmp_path: Path):
    main_mod = _app(monkeypatch, tmp_path, "newbie@x.com")
    with TestClient(main_mod.app) as client:
        denied = client.get("/")
        assert denied.status_code == 403
        assert "newbie@x.com" in denied.text          # identity card
        assert "CatDV Annotator" in denied.text        # rebrand (not "Archive AI")
        r = client.post("/access/request", headers={"HX-Request": "true"})
        assert r.status_code == 200
        assert "review" in r.text.lower()
        # appears as a pending request to the admin
    main2 = _app(monkeypatch, tmp_path, "boss@x.com")
    with TestClient(main2.app) as client:
        assert "newbie@x.com" in client.get("/admin/access?status=requested").text


def test_request_access_is_allowlisted(monkeypatch, tmp_path: Path):
    main_mod = _app(monkeypatch, tmp_path, "newbie@x.com")
    with TestClient(main_mod.app) as client:
        # the POST itself must not be gated (would loop)
        assert client.post("/access/request").status_code == 200
