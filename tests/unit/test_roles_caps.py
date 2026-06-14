# tests/unit/test_roles_caps.py
"""ROLE_CAPS is the single source of truth for role→permission (spec
2026-06-14-iap-roles-admin-console-design.md): Admin VPAM, Annotator VPA,
Publisher VP, Viewer V."""
from backend.app.auth.roles import ROLE_CAPS, ROLE_ORDER, has_permission


def test_capability_ladder():
    assert ROLE_CAPS["viewer"] == {"view"}
    assert ROLE_CAPS["publisher"] == {"view", "publish"}
    assert ROLE_CAPS["annotator"] == {"view", "publish", "run"}
    assert ROLE_CAPS["admin"] == {"view", "publish", "run", "manage"}


def test_role_order_admin_first():
    assert ROLE_ORDER == ["admin", "annotator", "publisher", "viewer"]


def test_has_permission_is_fail_closed_for_unknown_role():
    assert has_permission("annotator", "run") is True
    assert has_permission("viewer", "run") is False
    assert has_permission(None, "view") is False
    assert has_permission("wizard", "view") is False
