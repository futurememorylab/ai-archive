"""PR2a — IAP JWT verification (backend/app/auth/adapters/iap.py).

The signed ``X-Goog-IAP-JWT-Assertion`` Google injects in front of Cloud Run
is cryptographically verified (signature + audience) before its ``email``
claim is trusted. Everything fails closed: missing header, missing audience
config, bad signature, or a token with no email all raise rather than admit.

Google's cert fetch / signature check (``id_token.verify_token``) is mocked
here — the *live* audience string is confirmed against the deployed token, not
asserted in a unit test.
"""

import pytest
from starlette.requests import Request

from backend.app.auth.adapters import iap
from backend.app.auth.errors import NotAuthenticated
from backend.app.auth.models import CurrentUser
from backend.app.settings import Settings

# Placeholder; verify_token is mocked so the exact value is irrelevant here.
_AUD = "/projects/204842536530/locations/europe-west3/services/catdv-annotator"


def _settings(**over) -> Settings:
    base = dict(
        catdv_base_url="http://localhost:0",
        catdv_catalog_id=881507,
        gcp_project_id="p",
        gcs_bucket_name="b",
        auth_backend="iap",
        iap_audience=_AUD,
    )
    base.update(over)
    return Settings(**base)


def _request(headers: dict[str, str] | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw})


def test_missing_assertion_header_fails_closed():
    with pytest.raises(NotAuthenticated):
        iap.current_user(_request(), _settings())


def test_unconfigured_audience_fails_closed(monkeypatch):
    # Verifying against an empty audience is a verification bypass — refuse it.
    # (verify_token is mocked so the discovery decode stays offline.)
    monkeypatch.setattr(iap.id_token, "verify_token", lambda *a, **k: {"aud": "a", "email": "e"})
    s = _settings(iap_audience=None)
    with pytest.raises(RuntimeError):
        iap.current_user(_request({iap.IAP_JWT_HEADER: "h.p.s"}), s)


def test_unset_audience_logs_discovered_aud_then_fails_closed(monkeypatch, caplog):
    # The one-time discovery aid: signature-only decode logs the aud claim so the
    # operator can set IAP_AUDIENCE, but we STILL fail closed (never admit).
    monkeypatch.setattr(
        iap.id_token, "verify_token",
        lambda *a, **k: {"aud": "DISCOVERED-AUD", "email": "x@y.com"},
    )
    s = _settings(iap_audience=None)
    with caplog.at_level("WARNING"):
        with pytest.raises(RuntimeError):
            iap.current_user(_request({iap.IAP_JWT_HEADER: "h.p.s"}), s)
    assert "DISCOVERED-AUD" in caplog.text


def test_valid_assertion_returns_user(monkeypatch):
    monkeypatch.setattr(iap.id_token, "verify_token", lambda *a, **k: {"email": "x@y.com"})
    user = iap.current_user(_request({iap.IAP_JWT_HEADER: "h.p.s"}), _settings())
    assert user == CurrentUser(email="x@y.com")


def test_audience_is_passed_to_verification(monkeypatch):
    seen = {}

    def fake_verify(token, request, audience=None, certs_url=None):
        seen["token"] = token
        seen["audience"] = audience
        return {"email": "x@y.com"}

    monkeypatch.setattr(iap.id_token, "verify_token", fake_verify)
    iap.current_user(_request({iap.IAP_JWT_HEADER: "the.jwt.token"}), _settings())
    assert seen["token"] == "the.jwt.token"
    assert seen["audience"] == _AUD


def test_invalid_assertion_fails_closed(monkeypatch):
    def boom(*a, **k):
        raise ValueError("bad signature")

    monkeypatch.setattr(iap.id_token, "verify_token", boom)
    with pytest.raises(NotAuthenticated):
        iap.current_user(_request({iap.IAP_JWT_HEADER: "h.p.s"}), _settings())


def test_assertion_without_email_claim_fails_closed(monkeypatch):
    monkeypatch.setattr(iap.id_token, "verify_token", lambda *a, **k: {"sub": "123"})
    with pytest.raises(NotAuthenticated):
        iap.current_user(_request({iap.IAP_JWT_HEADER: "h.p.s"}), _settings())
