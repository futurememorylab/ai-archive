"""Route-level authorization guards. The gate middleware already enforces
"must have an active role" app-wide; these add the finer checks (admin-only
console, run-capable AI endpoints). Both read the CurrentUser the gate stashed
on request.state and fail closed with 403.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from backend.app.auth.models import CurrentUser


def require_permission(request: Request, cap: str) -> CurrentUser:
    user = getattr(request.state, "current_user", None)
    if user is None or not user.has(cap):
        raise HTTPException(403, f"requires '{cap}' permission")
    return user


def require_role(request: Request, role: str) -> CurrentUser:
    user = getattr(request.state, "current_user", None)
    if user is None or user.role != role:
        raise HTTPException(403, f"requires '{role}' role")
    return user
