"""Auth seam exceptions."""

from __future__ import annotations


class NotAuthenticated(Exception):
    """No trustworthy identity could be established for the request — the IAP
    assertion was missing, malformed, or failed verification.

    Distinct from a *configuration* error (e.g. ``AUTH_BACKEND=iap`` without an
    ``IAP_AUDIENCE``), which raises ``RuntimeError``. The ``_auth_gate``
    middleware maps both to a fail-closed denial on non-allow-listed paths
    (ADR 0085).
    """
