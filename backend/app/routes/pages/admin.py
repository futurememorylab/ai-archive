"""Admin console: data-driven editing of editable enumerations (issue #13)."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from backend.app.deps import get_core_ctx
from backend.app.routes.pages.templates import templates
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
    ctx = get_core_ctx(request)
    definitions = await ctx.enum_service.definitions(editable_only=True)
    active_key = definitions[0].key if definitions else None
    view = await _enum_view(ctx, active_key) if active_key else None
    return templates.TemplateResponse(
        request,
        "pages/admin.html",
        {
            "rail_active": "admin",
            "definitions": definitions,
            "active_key": active_key,
            "view": view,
        },
    )


@router.get("/admin/enums/{key}", response_class=HTMLResponse)
async def admin_enum_table(request: Request, key: str):
    ctx = get_core_ctx(request)
    view = await _enum_view(ctx, key)
    return templates.TemplateResponse(request, "pages/_admin_enum_table.html", view)
