"""IAP identity backend (cloud).

Google IAP sits in front of Cloud Run and injects a *signed* identity
assertion (``X-Goog-IAP-JWT-Assertion``) on every request. This adapter
**cryptographically verifies** that assertion — signature against Google's IAP
public keys + the configured audience — before trusting its ``email`` claim.

Everything fails closed: a missing header or a verification failure raises
``NotAuthenticated`` (no fail-open), and an unconfigured audience raises
``RuntimeError`` rather than verify against an empty audience. The plaintext
``X-Goog-Authenticated-User-Email`` header is never trusted on its own.
See ADR 0084 §security.
"""

from __future__ import annotations

import logging

from fastapi import Request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from backend.app.auth.errors import NotAuthenticated
from backend.app.auth.models import CurrentUser
from backend.app.settings import Settings

log = logging.getLogger(__name__)

# The signed assertion IAP injects.
IAP_JWT_HEADER = "x-goog-iap-jwt-assertion"
# Google's public keys for IAP-signed JWTs (distinct from the OAuth2 certs).
IAP_CERTS_URL = "https://www.gstatic.com/iap/verify/public_key"


def current_user(request: Request, settings: Settings) -> CurrentUser:
    audience = settings.iap_audience
    if not audience:
        # One-time discovery aid (ADR 0084/0085): the exact JWT audience for
        # direct Cloud Run IAP is not authoritatively documented, so it is
        # discovered from a live token. Signature-only decode (audience check
        # skipped) to LOG the aud, then fail closed — never ADMIT against an
        # unconfigured audience. Diagnostic: also surface a missing/renamed
        # header or a decode failure, since the IAP verify path runs for real.
        assertion = request.headers.get(IAP_JWT_HEADER)
        if not assertion:
            goog = [k for k in request.headers.keys() if k.lower().startswith("x-goog")]
            log.warning(
                "IAP_AUDIENCE unset and no %s header on the request; x-goog-* headers present: %s",
                IAP_JWT_HEADER,
                goog,
            )
        else:
            try:
                claims = id_token.verify_token(
                    assertion, google_requests.Request(), certs_url=IAP_CERTS_URL
                )
                log.warning(
                    "IAP_AUDIENCE is unset; discovered aud=%r from a live IAP "
                    "assertion — set IAP_AUDIENCE to this value and redeploy "
                    "(ADR 0085).",
                    claims.get("aud"),
                )
            except Exception as exc:  # noqa: BLE001 — best-effort; still fail closed
                log.warning(
                    "IAP_AUDIENCE unset; assertion present but signature-only decode failed: %r",
                    exc,
                )
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
