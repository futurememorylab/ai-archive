# tests/unit/test_roles_caps.py
"""ROLE_CAPS is the single source of truth for role→permission. Two roles:
'admin' (manage access + everything) and 'member' (a regular non-admin user)."""
from backend.app.auth.roles import ROLE_CAPS, ROLE_ORDER, has_permission


def test_two_role_capability_model():
    assert ROLE_CAPS["member"] == {"view", "publish", "run"}
    assert ROLE_CAPS["admin"] == {"view", "publish", "run", "manage"}


def test_role_order_admin_first():
    assert ROLE_ORDER == ["admin", "member"]


def test_has_permission_is_fail_closed_for_unknown_role():
    assert has_permission("member", "run") is True
    assert has_permission("member", "manage") is False  # manage is admin-only
    assert has_permission(None, "view") is False
    assert has_permission("wizard", "view") is False
