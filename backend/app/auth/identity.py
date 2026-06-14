"""The auth seam.

The whole app asks for the current user through ``get_current_user`` /
``resolve_user`` and gets a ``CurrentUser`` back — nothing else knows *how*
identity is established. The active backend is chosen by
``settings.auth_backend``; only modules under ``backend/app/auth/adapters/``
may touch IAP/OAuth specifics (enforced by
``tests/unit/test_auth_seam_boundary.py``). This one seam is what keeps the
IAP↔app-OAuth and cloud↔local choices swappable with bounded effort (ADR 0078).

PR1 ships the seam + the ``dev`` adapter; the ``iap`` adapter is a fail-closed
placeholder until PR2. The dependency is **not yet wired into any route** —
PR1 is behaviour-neutral.
"""

from __future__ import annotations

from fastapi import Request

from backend.app.auth.adapters import dev as _dev
from backend.app.auth.adapters import iap as _iap
from backend.app.auth.models import CurrentUser
from backend.app.settings import Settings

__all__ = ["CurrentUser", "resolve_user", "get_current_user"]


def resolve_user(request: Request, settings: Settings) -> CurrentUser:
    """Dispatch to the configured identity backend.

    Fail closed: any backend that cannot positively establish identity raises,
    and an unrecognised ``auth_backend`` raises too — we never return an
    unauthenticated/unverified user from here.
    """
    backend = settings.auth_backend
    if backend == "dev":
        return _dev.current_user(request, settings)
    if backend == "iap":
        return _iap.current_user(request, settings)
    raise RuntimeError(f"unknown AUTH_BACKEND: {backend!r}")


async def get_current_user(request: Request) -> CurrentUser:
    """FastAPI dependency: read settings off the CoreCtx on ``app.state``
    (mirrors ``deps.get_core_ctx``) and resolve the current user."""
    settings: Settings = request.app.state.core_ctx.settings
    return resolve_user(request, settings)
