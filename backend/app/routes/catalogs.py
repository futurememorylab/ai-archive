"""Active-catalog picker (#124): topbar partial + switch endpoint.

CoreCtx only — the list is DB-cached (services/active_catalog), so both work
offline; the live context is used opportunistically to refresh a stale list.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from backend.app.deps import get_core_ctx
from backend.app.routes.pages.templates import templates
from backend.app.services.active_catalog import COOKIE, active_catalog_id, list_catalogs

router = APIRouter(tags=["catalogs"])

_ONE_YEAR = 365 * 24 * 3600


@router.get("/ui/catalog-picker", response_class=HTMLResponse)
async def catalog_picker(request: Request):
    core = get_core_ctx(request)
    active_id = active_catalog_id(request, core.settings)
    catalogs = await list_catalogs(core, request.app.state.live_ctx, active_id)
    active_name = next(c.name for c in catalogs if c.id == active_id)
    resp = templates.TemplateResponse(
        request,
        "pages/_catalog_picker.html",
        {"catalogs": catalogs, "active_id": active_id, "active_name": active_name},
    )
    resp.headers["Cache-Control"] = "no-store"
    return resp


@router.post("/api/catalog/active")
async def set_active_catalog(request: Request, catalog_id: str = Form("")):
    core = get_core_ctx(request)
    active_id = active_catalog_id(request, core.settings)
    known = {c.id for c in await list_catalogs(core, request.app.state.live_ctx, active_id)}
    if not catalog_id.isdigit() or catalog_id not in known:
        raise HTTPException(400, f"Unknown catalog: {catalog_id or '(empty)'}")
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(COOKIE, catalog_id, max_age=_ONE_YEAR, httponly=True, samesite="lax", path="/")
    return resp
