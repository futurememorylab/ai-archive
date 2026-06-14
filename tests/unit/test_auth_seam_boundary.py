"""Boundary guard for the auth seam (ADR 0078): IAP/OAuth *implementation
specifics* must live only under ``backend/app/auth/adapters/``. Everything
else asks for identity through ``get_current_user`` and never touches the
IAP header name, the IAP verification endpoint, or an OAuth/JWT library.

This is a ratchet (same spirit as test_design_language_guard.py): it freezes
the boundary so a future "just read the header here" shortcut trips CI and is
routed back through the seam. ``"iap"`` as a backend *name* and the adapter
module import are fine — only the implementation markers below are scoped.
"""

import re
from pathlib import Path

APP = Path("backend/app")
ADAPTERS = APP / "auth" / "adapters"

# IAP user-identity markers that must appear only inside the adapters.
# NB: deliberately NOT `google.auth` / `google.oauth2` — those are used app-wide
# for GCS/Vertex *service-account* credentials (context.py, services/gcs.py),
# which is infrastructure auth, unrelated to IAP user login. The markers below
# are specific to reading/verifying the IAP-signed *user* identity.
_FORBIDDEN = re.compile(
    r"x-goog-iap-jwt-assertion"
    r"|x-goog-authenticated-user-email"
    r"|iap\.googleapis\.com"
    r"|gcp-sa-iap"
    r"|google\.oauth2\.id_token"
    r"|\bimport jwt\b",
    re.IGNORECASE,
)


def test_only_adapters_reference_iap_oauth_specifics():
    offenders: dict[str, list[str]] = {}
    for path in sorted(APP.rglob("*.py")):
        if ADAPTERS in path.parents:
            continue
        hits = _FORBIDDEN.findall(path.read_text(encoding="utf-8"))
        if hits:
            offenders[str(path)] = sorted(set(h.lower() for h in hits))
    assert not offenders, (
        "IAP/OAuth specifics found outside backend/app/auth/adapters/ — route "
        f"identity through get_current_user() instead (ADR 0078): {offenders}"
    )
