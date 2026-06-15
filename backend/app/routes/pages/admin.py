"""Admin console: data-driven editing of editable enumerations (issue #13).

Admin-only: the auth gate already requires an active role to reach `/admin`,
and `require_role("admin")` narrows every handler to the `manage` capability
(ADR 0082). The Access & Permissions section lives in `admin_access.py`.
"""

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from backend.app.auth.guards import require_role
from backend.app.deps import get_core_ctx
from backend.app.routes.pages.admin_access import _members_ctx as _access_members_ctx
from backend.app.routes.pages.templates import templates
from backend.app.services.enum_service import EnumError
from backend.app.services.errors import humanise
from backend.app.services.pricing import RATE_CARDS

router = APIRouter(tags=["pages"])


async def _enum_view(ctx, key: str) -> dict:
    defs = {d.key: d for d in await ctx.enum_service.definitions(editable_only=True)}
    if key not in defs:
        raise HTTPException(404, f"no editable enum {key!r}")
    values = await ctx.enum_service.values(key)
    is_model_enum = key == "gemini_generation_model"
    rows = [
        {
            "value": v.value,
            "label": v.label,
            "enabled": v.enabled,
            "is_default": v.is_default,
            "no_rate_card": is_model_enum and v.value not in RATE_CARDS,
        }
        for v in values
    ]
    return {"definition": defs[key], "rows": rows, "key": key}


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    require_role(request, "admin")
    ctx = get_core_ctx(request)
    definitions = await ctx.enum_service.definitions(editable_only=True)
    # Default tab = Access & Permissions, rendered server-side. The role pickers
    # are popover() components + an add-member modal; rendering them on the full
    # page load lets Alpine init them once (HTMX-injecting double-binds them and
    # the dropdown sticks open). Enum tabs still load their tables via HTMX.
    data = await _access_members_ctx(request)
    data.update(
        {"rail_active": "admin", "definitions": definitions, "active": "access", "active_key": None}
    )
    return templates.TemplateResponse(request, "pages/admin.html", data)


@router.get("/admin/enums/{key}", response_class=HTMLResponse)
async def admin_enum_table(request: Request, key: str):
    require_role(request, "admin")
    ctx = get_core_ctx(request)
    view = await _enum_view(ctx, key)
    return templates.TemplateResponse(request, "pages/_admin_enum_table.html", view)


async def _table_response(request: Request, ctx, key: str, *, status_code: int = 200):
    view = await _enum_view(ctx, key)
    return templates.TemplateResponse(
        request, "pages/_admin_enum_table.html", view, status_code=status_code
    )


@router.post("/admin/enums/{key}/values", response_class=HTMLResponse)
async def admin_add_value(
    request: Request,
    key: str,
    value: str = Form(...),
    label: str | None = Form(None),
):
    require_role(request, "admin")
    ctx = get_core_ctx(request)
    try:
        await ctx.enum_service.add_value(key, value.strip(), label=(label or None))
    except EnumError as exc:
        raise HTTPException(400, humanise(exc)) from exc
    return await _table_response(request, ctx, key)


@router.post("/admin/enums/{key}/values/{value}/enabled", response_class=HTMLResponse)
async def admin_toggle_enabled(
    request: Request,
    key: str,
    value: str,
    enabled: bool = Form(...),
):
    require_role(request, "admin")
    ctx = get_core_ctx(request)
    try:
        await ctx.enum_service.set_enabled(key, value, enabled=enabled)
    except EnumError as exc:
        raise HTTPException(400, humanise(exc)) from exc
    return await _table_response(request, ctx, key)


@router.post("/admin/enums/{key}/values/{value}/default", response_class=HTMLResponse)
async def admin_set_default(request: Request, key: str, value: str):
    require_role(request, "admin")
    ctx = get_core_ctx(request)
    try:
        await ctx.enum_service.set_default(key, value)
    except EnumError as exc:
        raise HTTPException(400, humanise(exc)) from exc
    return await _table_response(request, ctx, key)


@router.delete("/admin/enums/{key}/values/{value}", response_class=HTMLResponse)
async def admin_remove_value(request: Request, key: str, value: str):
    require_role(request, "admin")
    ctx = get_core_ctx(request)
    try:
        await ctx.enum_service.remove_value(key, value)
    except EnumError as exc:
        raise HTTPException(400, humanise(exc)) from exc
    return await _table_response(request, ctx, key)
