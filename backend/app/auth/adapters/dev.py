"""Dev identity backend — local development without IAP.

Returns a single configured operator identity (``settings.dev_user_email``)
so the app is usable on 127.0.0.1, where Google IAP does not exist. Never the
backend in cloud (there ``AUTH_BACKEND=iap``). See ADR 0084.
"""

from __future__ import annotations

from fastapi import Request

from backend.app.auth.models import CurrentUser
from backend.app.settings import Settings


def current_user(request: Request, settings: Settings) -> CurrentUser:
    return CurrentUser(email=settings.dev_user_email)
