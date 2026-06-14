"""The single source of truth for the role model (spec
2026-06-14-iap-roles-admin-console-design.md). Imported by the gate, the
CurrentUser capability helpers, the admin console UI, and the guards — so the
ladder is defined exactly once.
"""

from __future__ import annotations

# role -> set of capabilities. View(V) Publish(P) Run AI(A) Manage access(M).
ROLE_CAPS: dict[str, set[str]] = {
    "admin": {"view", "publish", "run", "manage"},
    "annotator": {"view", "publish", "run"},
    "publisher": {"view", "publish"},
    "viewer": {"view"},
}

# Display order (privilege descending), used by the table + role pickers.
ROLE_ORDER: list[str] = ["admin", "annotator", "publisher", "viewer"]

# Human labels + one-line descriptions for the role picker / pills.
ROLE_META: dict[str, dict[str, str]] = {
    "admin": {"label": "Admin", "desc": "Full control — manage members & access"},
    "annotator": {"label": "Annotator", "desc": "Run AI analysis, publish & view"},
    "publisher": {"label": "Publisher", "desc": "Publish & view analyses"},
    "viewer": {"label": "Viewer", "desc": "View analyses only"},
}

# Ordered (capability, letter) pairs for the V·P·A·M permission dots.
CAP_ORDER: list[tuple[str, str]] = [
    ("view", "V"),
    ("publish", "P"),
    ("run", "A"),
    ("manage", "M"),
]


def has_permission(role: str | None, cap: str) -> bool:
    """Fail-closed: an unknown/None role has no capabilities."""
    return cap in ROLE_CAPS.get(role or "", set())
