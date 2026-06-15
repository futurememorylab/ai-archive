# tests/unit/test_current_user_caps.py
"""CurrentUser derives permissions from role via ROLE_CAPS — no stored
permission state to drift."""
from backend.app.auth.models import CurrentUser


def test_admin_has_all_caps_and_is_admin():
    u = CurrentUser(email="a@x.com", role="admin")
    assert u.has("manage") and u.has("run") and u.is_admin


def test_member_can_use_app_but_lacks_manage():
    u = CurrentUser(email="m@x.com", role="member")
    assert u.has("view") and u.has("run") and not u.has("manage")
    assert u.is_admin is False


def test_unroled_user_has_nothing():
    u = CurrentUser(email="x@x.com", role=None)
    assert u.permissions == frozenset()
    assert not u.has("view")
