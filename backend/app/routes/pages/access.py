"""Access-control pages — the app-rendered states under IAP access control
(spec 2026-06-14-iap-roles-admin-console-design.md): state "denied" (a
signed-in user who passed the IAP gate but has no role in `user_roles`),
state "requested" (user submitted an access request, awaiting admin review),
and state "error". Google/IAP owns the sign-in + redirect states upstream,
so they are not rendered here.

The GET /access route is on the auth gate's allow-list (it IS the page the
gate redirects to — gating it would loop). The POST /access/request is also
allow-listed (a denied user must be able to submit a request without being
blocked by the gate).
"""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from backend.app.routes.pages.templates import templates

router = APIRouter()


@router.get("/access", response_class=HTMLResponse)
async def access(
    request: Request,
    state: Literal["denied", "error", "requested"] = "denied",
    email: str | None = None,
):
    view = state if state in ("error", "requested") else "denied"
    return templates.TemplateResponse(
        request,
        "pages/access.html",
        {"state": view, "email": email},
    )


@router.post("/access/request", response_class=HTMLResponse)
async def request_access(request: Request):
    """Record an access request from a reached-but-unroled user. Allow-listed
    (the gate must not block this — it's the one action a denied user can take).
    No email is sent; admins see the request in the console."""
    from backend.app.deps import get_core_ctx

    user = getattr(request.state, "current_user", None)
    if user is None or not user.email:
        # No verified identity → nothing to record. Fail closed but quietly.
        raise HTTPException(403, "no identity")
    ctx = get_core_ctx(request)
    await ctx.user_roles_repo.record_request(ctx.db, user.email)
    return templates.TemplateResponse(
        request, "pages/access.html", {"state": "requested", "email": user.email}
    )
