"""PR1 — the auth seam (spec docs/specs/2026-06-13-iap-access-control-design.md,
ADR 0078).

A behaviour-neutral identity layer: a ``CurrentUser`` value + a
``resolve_user`` / ``get_current_user`` dependency that dispatches to a
per-backend adapter chosen by ``settings.auth_backend``. The ``dev`` adapter
makes local dev work without IAP; the ``iap`` adapter is a fail-closed
placeholder until PR2 wires real JWT verification. The "only adapters touch
IAP/OAuth specifics" boundary is enforced by ``test_auth_seam_boundary.py``.
"""

from types import SimpleNamespace

import pytest
from starlette.requests import Request

from backend.app.auth.identity import CurrentUser, get_current_user, resolve_user
from backend.app.settings import Settings


def _settings(**over) -> Settings:
    base = dict(
        catdv_base_url="http://localhost:0",
        catdv_catalog_id=881507,
        gcp_project_id="p",
        gcs_bucket_name="b",
    )
    base.update(over)
    return Settings(**base)


def _request(headers: dict[str, str] | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw})


def test_current_user_authenticated_with_email():
    assert CurrentUser(email="a@b.com").is_authenticated is True


def test_current_user_unauthenticated_when_email_blank():
    assert CurrentUser(email="").is_authenticated is False


def test_dev_backend_returns_configured_user():
    s = _settings(auth_backend="dev", dev_user_email="me@local")
    user = resolve_user(_request(), s)
    assert user.email == "me@local"
    assert user.is_authenticated


def test_iap_backend_fails_closed_when_audience_unconfigured():
    """Dispatch reaches the iap adapter; with IAP_AUDIENCE unset it fails
    closed rather than verify against an empty audience. Full IAP verification
    paths live in test_iap_adapter.py."""
    s = _settings(auth_backend="iap")  # iap_audience defaults to None
    # No assertion header → the adapter's one-time aud-discovery decode is skipped
    # (it's offline-only here); dispatch still fails closed with RuntimeError.
    req = _request()
    with pytest.raises(RuntimeError):
        resolve_user(req, s)


def test_unknown_backend_fails_closed():
    s = _settings()
    # setattr (not assignment) to evade the Literal["dev","iap"] type and reach
    # the dispatcher's defensive else-branch at runtime.
    setattr(s, "auth_backend", "bogus")  # noqa: B010
    with pytest.raises(RuntimeError):
        resolve_user(_request(), s)


async def test_get_current_user_reads_settings_from_app_state():
    """The FastAPI dependency pulls settings off the CoreCtx on app.state and
    delegates to resolve_user (mirrors deps.get_core_ctx)."""
    s = _settings(auth_backend="dev", dev_user_email="wired@local")
    req = _request()
    req.scope["app"] = SimpleNamespace(
        state=SimpleNamespace(core_ctx=SimpleNamespace(settings=s))
    )
    user = await get_current_user(req)
    assert user.email == "wired@local"
