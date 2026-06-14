# backend/app/routes/pages/admin.py
"""Admin console — Access & Permissions (spec
2026-06-14-iap-roles-admin-console-design.md). Admin-only. Google IAP owns the
gate; this page manages the app-side user_roles (what a reached user may do).
'Add member' pre-assigns a role (status 'invited') — it does NOT open the
Google gate; a human admin must also add them to the Google Group.

CRUD endpoints return the members-table partial on HX-Request and push a toast;
never location.reload(). Self-protection + last-admin guard are enforced
server-side, not just hidden in the UI.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from backend.app.auth.guards import require_role
from backend.app.auth.roles import ROLE_CAPS, ROLE_META, ROLE_ORDER
from backend.app.deps import get_core_ctx
from backend.app.routes.pages.templates import templates

router = APIRouter()


def _norm(email: str) -> str:
    return email.strip().lower()


async def _members_ctx(request: Request, *, role=None, status=None, query=None) -> dict:
    ctx = get_core_ctx(request)
    members = await ctx.user_roles_repo.list_members(
        ctx.db, role=role or None, status=status or None, query=query or None
    )
    admins = sum(1 for m in members if m["role"] == "admin")
    pending = sum(1 for m in members if m["status"] == "requested")
    me = request.state.current_user.email
    return {
        "members": members,
        "me": me,
        "counts": {"members": len(members), "admins": admins, "pending": pending},
        "role_order": ROLE_ORDER,
        "role_meta": ROLE_META,
        "role_caps": ROLE_CAPS,
        "filters": {"role": role or "", "status": status or "", "query": query or ""},
    }


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, role: str = "", status: str = "", q: str = ""):
    require_role(request, "admin")
    data = await _members_ctx(request, role=role, status=status, query=q)
    template = "pages/_admin_members.html" if request.headers.get("hx-request") else "pages/admin.html"
    return templates.TemplateResponse(request, template, data)


@router.post("/admin/users", response_class=HTMLResponse)
async def add_member(
    request: Request,
    email: str = Form(...),
    role: str = Form(...),
    display_name: str = Form(""),
):
    require_role(request, "admin")
    if role not in ROLE_CAPS:
        raise HTTPException(400, "unknown role")
    if "@" not in email:
        raise HTTPException(400, "invalid email")
    ctx = get_core_ctx(request)
    existing = await ctx.user_roles_repo.get(ctx.db, email)
    # An access-request becomes a real grant; a brand-new email becomes 'invited'.
    status = "active" if existing and existing["status"] == "requested" else "invited"
    await ctx.user_roles_repo.upsert_role(
        ctx.db, email, role, status=status, granted_by=request.state.current_user.email,
        display_name=display_name or None,
    )
    data = await _members_ctx(request)
    return templates.TemplateResponse(request, "pages/_admin_members.html", data)


@router.patch("/admin/users/{email}", response_class=HTMLResponse)
async def change_role(request: Request, email: str, role: str = Form(...)):
    require_role(request, "admin")
    if role not in ROLE_CAPS:
        raise HTTPException(400, "unknown role")
    ctx = get_core_ctx(request)
    target = _norm(email)
    if target == _norm(request.state.current_user.email):
        raise HTTPException(403, "you can't change your own role")
    current = await ctx.user_roles_repo.get(ctx.db, target)
    if current is None:
        raise HTTPException(404, "no such member")
    # last-admin guard: never let the count of admins reach zero.
    if current["role"] == "admin" and role != "admin":
        if await ctx.user_roles_repo.count_admins(ctx.db) <= 1:
            raise HTTPException(409, "can't demote the last admin")
    await ctx.user_roles_repo.upsert_role(
        ctx.db, target, role, status=current["status"],
        granted_by=request.state.current_user.email,
    )
    data = await _members_ctx(request)
    return templates.TemplateResponse(request, "pages/_admin_members.html", data)


@router.delete("/admin/users/{email}", response_class=HTMLResponse)
async def revoke(request: Request, email: str):
    require_role(request, "admin")
    ctx = get_core_ctx(request)
    target = _norm(email)
    if target == _norm(request.state.current_user.email):
        raise HTTPException(403, "you can't revoke your own access")
    current = await ctx.user_roles_repo.get(ctx.db, target)
    if current is None:
        raise HTTPException(404, "no such member")
    if current["role"] == "admin" and await ctx.user_roles_repo.count_admins(ctx.db) <= 1:
        raise HTTPException(409, "can't revoke the last admin")
    await ctx.user_roles_repo.delete(ctx.db, target)
    data = await _members_ctx(request)
    return templates.TemplateResponse(request, "pages/_admin_members.html", data)
