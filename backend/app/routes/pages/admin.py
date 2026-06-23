"""Admin console: data-driven editing of editable enumerations (issue #13).

Admin-only: the auth gate already requires an active role to reach `/admin`,
and `require_role("admin")` narrows every handler to the `manage` capability
(ADR 0085). The Access & Permissions section lives in `admin_access.py`.
"""

from typing import get_args

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from backend.app.auth.guards import require_role
from backend.app.deps import get_core_ctx
from backend.app.models.media import MediaResolution
from backend.app.routes.pages.admin_access import _members_ctx as _access_members_ctx
from backend.app.routes.pages.templates import templates
from backend.app.services.enum_service import EnumError
from backend.app.services.errors import humanise

router = APIRouter(tags=["pages"])

GEMINI_MODEL_KEY = "gemini_generation_model"


async def _enum_view(ctx, key: str) -> dict:
    defs = {d.key: d for d in await ctx.enum_service.definitions(editable_only=True)}
    if key not in defs:
        raise HTTPException(404, f"no editable enum {key!r}")
    values = await ctx.enum_service.values(key)
    rows = [
        {"value": v.value, "label": v.label, "enabled": v.enabled, "is_default": v.is_default}
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


async def _models_view(ctx) -> dict:
    """Spine = the Gemini model catalog (the editable enum); each model joined to
    its model_config rate card (may be absent)."""
    cards = {r.model: r for r in await ctx.pricing_service.rows()}
    rows = []
    for v in await ctx.enum_service.values(GEMINI_MODEL_KEY):
        c = cards.get(v.value)
        rows.append(
            {
                "model": v.value,
                "enabled": v.enabled,
                "is_default": v.is_default,
                "has_card": c is not None,
                "input_text_video_image_per_1m": c.input_text_video_image_per_1m if c else "",
                "input_audio_per_1m": c.input_audio_per_1m if c else "",
                "input_cached_per_1m": c.input_cached_per_1m if c else "",
                "output_per_1m": c.output_per_1m if c else "",
                "default_media_resolution": c.default_media_resolution if c else "—",
            }
        )
    return {"rows": rows}


async def _models_response(request: Request, ctx):
    return templates.TemplateResponse(
        request, "pages/_admin_models_table.html", await _models_view(ctx)
    )


@router.get("/admin/models", response_class=HTMLResponse)
async def admin_models_table(request: Request):
    require_role(request, "admin")
    ctx = get_core_ctx(request)
    return await _models_response(request, ctx)


@router.post("/admin/models", response_class=HTMLResponse)
async def admin_add_model(
    request: Request,
    model: str = Form(...),
    label: str | None = Form(None),
):
    require_role(request, "admin")
    ctx = get_core_ctx(request)
    try:
        await ctx.enum_service.add_value(GEMINI_MODEL_KEY, model.strip(), label=(label or None))
    except EnumError as exc:
        raise HTTPException(400, humanise(exc)) from exc
    return await _models_response(request, ctx)


@router.post("/admin/models/{model}/rates", response_class=HTMLResponse)
async def admin_edit_model_rates(
    request: Request,
    model: str,
    input_text_video_image_per_1m: float = Form(..., ge=0),
    input_audio_per_1m: float = Form(..., ge=0),
    input_cached_per_1m: float = Form(..., ge=0),
    output_per_1m: float = Form(..., ge=0),
):
    require_role(request, "admin")
    ctx = get_core_ctx(request)
    catalog = {v.value for v in await ctx.enum_service.values(GEMINI_MODEL_KEY)}
    if model not in catalog:
        raise HTTPException(404, f"unknown model {model!r}")
    await ctx.pricing_service.set_rates(
        model,
        input_text_video_image_per_1m=input_text_video_image_per_1m,
        input_audio_per_1m=input_audio_per_1m,
        input_cached_per_1m=input_cached_per_1m,
        output_per_1m=output_per_1m,
    )
    return await _models_response(request, ctx)


@router.post("/admin/models/{model}/resolution", response_class=HTMLResponse)
async def admin_set_model_resolution(
    request: Request, model: str, media_resolution: str = Form(...)
):
    require_role(request, "admin")
    ctx = get_core_ctx(request)
    if media_resolution not in get_args(MediaResolution):
        raise HTTPException(422, f"bad media_resolution {media_resolution!r}")
    has_card = any(r.model == model for r in await ctx.pricing_service.rows())
    if not has_card:
        raise HTTPException(404, f"no rate card for {model!r}")
    await ctx.pricing_service.set_resolution(model, media_resolution)
    return await _models_response(request, ctx)


@router.post("/admin/models/{model}/default", response_class=HTMLResponse)
async def admin_model_set_default(request: Request, model: str):
    require_role(request, "admin")
    ctx = get_core_ctx(request)
    try:
        await ctx.enum_service.set_default(GEMINI_MODEL_KEY, model)
    except EnumError as exc:
        raise HTTPException(400, humanise(exc)) from exc
    return await _models_response(request, ctx)


@router.post("/admin/models/{model}/enabled", response_class=HTMLResponse)
async def admin_model_set_enabled(request: Request, model: str, enabled: bool = Form(...)):
    require_role(request, "admin")
    ctx = get_core_ctx(request)
    try:
        await ctx.enum_service.set_enabled(GEMINI_MODEL_KEY, model, enabled=enabled)
    except EnumError as exc:
        raise HTTPException(400, humanise(exc)) from exc
    return await _models_response(request, ctx)


@router.delete("/admin/models/{model}", response_class=HTMLResponse)
async def admin_remove_model(request: Request, model: str):
    require_role(request, "admin")
    ctx = get_core_ctx(request)
    try:
        await ctx.enum_service.remove_value(GEMINI_MODEL_KEY, model)
    except EnumError as exc:
        raise HTTPException(400, humanise(exc)) from exc
    await ctx.pricing_service.remove_model(model)
    return await _models_response(request, ctx)


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
