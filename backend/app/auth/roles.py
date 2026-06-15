"""The single source of truth for the role model (spec
2026-06-14-iap-roles-admin-console-design.md). Imported by the gate, the
CurrentUser capability helpers, the admin console UI, and the guards — so the
ladder is defined exactly once.
"""

from __future__ import annotations

# role -> set of capabilities. Two roles: 'admin' manages access (+ everything a
# member can do); 'member' is a regular, non-admin user of the app.
# Capabilities: View, Publish, Run AI, Manage access.
ROLE_CAPS: dict[str, set[str]] = {
    "admin": {"view", "publish", "run", "manage"},
    "member": {"view", "publish", "run"},
}

# Display order (privilege descending), used by the role pickers.
ROLE_ORDER: list[str] = ["admin", "member"]

# Human labels + one-line descriptions for the role picker / pills.
ROLE_META: dict[str, dict[str, str]] = {
    "admin": {"label": "Admin", "desc": "Full control — manage members & access"},
    "member": {"label": "Member", "desc": "Use the app — view, publish & run AI"},
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
