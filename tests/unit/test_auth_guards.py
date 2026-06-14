# tests/unit/test_auth_guards.py
"""Route guards read request.state.current_user (set by the gate) and raise
403 fail-closed when the capability/role is missing."""
import pytest
from types import SimpleNamespace
from fastapi import HTTPException

from backend.app.auth.guards import require_permission, require_role
from backend.app.auth.models import CurrentUser


def _req(user):
    return SimpleNamespace(state=SimpleNamespace(current_user=user))


def test_require_permission_allows_capable_user():
    u = CurrentUser(email="a@x.com", role="annotator")
    assert require_permission(_req(u), "run") is u


def test_require_permission_denies_incapable_user():
    with pytest.raises(HTTPException) as ei:
        require_permission(_req(CurrentUser(email="v@x.com", role="viewer")), "run")
    assert ei.value.status_code == 403


def test_guards_deny_when_no_user():
    with pytest.raises(HTTPException) as ei:
        require_role(_req(None), "admin")
    assert ei.value.status_code == 403
