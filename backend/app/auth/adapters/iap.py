"""IAP identity backend (cloud).

Google IAP sits in front of Cloud Run and injects a *signed* identity
assertion (``X-Goog-IAP-JWT-Assertion``) on every request. This adapter
**cryptographically verifies** that assertion — signature against Google's IAP
public keys + the configured audience — before trusting its ``email`` claim.

Everything fails closed: a missing header or a verification failure raises
``NotAuthenticated`` (no fail-open), and an unconfigured audience raises
``RuntimeError`` rather than verify against an empty audience. The plaintext
``X-Goog-Authenticated-User-Email`` header is never trusted on its own.
See ADR 0078 §security.
"""

from __future__ import annotations

from fastapi import Request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from backend.app.auth.errors import NotAuthenticated
from backend.app.auth.models import CurrentUser
from backend.app.settings import Settings

# The signed assertion IAP injects.
IAP_JWT_HEADER = "x-goog-iap-jwt-assertion"
# Google's public keys for IAP-signed JWTs (distinct from the OAuth2 certs).
IAP_CERTS_URL = "https://www.gstatic.com/iap/verify/public_key"


def current_user(request: Request, settings: Settings) -> CurrentUser:
    audience = settings.iap_audience
    if not audience:
        raise RuntimeError(
            "AUTH_BACKEND=iap but IAP_AUDIENCE is not configured — refusing to "
            "verify the IAP assertion against an empty audience."
        )
    assertion = request.headers.get(IAP_JWT_HEADER)
    if not assertion:
        raise NotAuthenticated(f"missing {IAP_JWT_HEADER} header")
    try:
        claims = id_token.verify_token(
            assertion,
            google_requests.Request(),
            audience=audience,
            certs_url=IAP_CERTS_URL,
        )
    except Exception as exc:  # any verification failure → no trusted identity
        raise NotAuthenticated("IAP assertion failed verification") from exc
    email = claims.get("email")
    if not email:
        raise NotAuthenticated("IAP assertion has no email claim")
    return CurrentUser(email=email)
