"""Admin console: data-driven editing of editable enumerations (issue #13).

Admin-only: the auth gate already requires an active role to reach `/admin`,
and `require_role("admin")` narrows every handler to the `manage` capability
(ADR 0085). The Access & Permissions section lives in `admin_access.py`.
"""

import time as _time
from typing import get_args

from fastapi import APIRouter, Body, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from backend.app.auth.guards import require_role
from backend.app.deps import get_core_ctx
from backend.app.models.media import MediaResolution
from backend.app.routes.jobs import start_job_in_background
from backend.app.routes.pages.admin_access import _members_ctx as _access_members_ctx
from backend.app.routes.pages.templates import templates
from backend.app.services.calibration import confidence_for_samples
from backend.app.services.enum_service import EnumError
from backend.app.services.errors import humanise
from backend.app.services.resolution import resolution_valid_for_kind
from backend.app.services.run_estimator import (
    estimate_for_clip_ids,
    media_kinds_for_clip_ids,
)

router = APIRouter(tags=["pages"])

GEMINI_MODEL_KEY = "gemini_generation_model"

# Calibration sweep dimensions: 3 resolutions × 2 repeats = 6 jobs per launch
# (each job runs the 3 chosen clips → 18 telemetry-only runs).
CALIBRATION_RESOLUTIONS = ("low", "medium", "high")
CALIBRATION_REPEATS = 2


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


async def _prompts_view(ctx) -> dict:
    rows = []
    for p in await ctx.prompts_repo.list_active(ctx.db):
        _p, versions = await ctx.prompts_repo.get_with_versions(ctx.db, p.id)
        for v in versions:
            stats = await ctx.run_telemetry_repo.stats_by_resolution(
                ctx.db, prompt_version_id=v.id
            )
            per_res = {
                res: {
                    "count": s["count"],
                    "cost_usd": s["cost_usd"],
                    "est_cost_usd": s["est_cost_usd"],
                    "confidence": confidence_for_samples(s["count"]),
                }
                for res, s in stats.items()
                if res is not None
            }
            _mc = await ctx.model_config_repo.get(ctx.db, v.model)
            pricing_missing = _mc is None or bool(_mc.removed)
            rows.append(
                {
                    "prompt_name": p.name,
                    "version_id": v.id,
                    "version_num": v.version_num,
                    "state": v.state,
                    "model": v.model,
                    "media_kind": p.media_kind,
                    "per_res": per_res,
                    "pricing_missing": pricing_missing,
                }
            )
    return {"rows": rows}


async def _prompts_response(request: Request, ctx):
    return templates.TemplateResponse(
        request, "pages/_admin_prompts_table.html", await _prompts_view(ctx)
    )


@router.get("/admin/prompts", response_class=HTMLResponse)
async def admin_prompts_table(request: Request):
    require_role(request, "admin")
    ctx = get_core_ctx(request)
    return await _prompts_response(request, ctx)


@router.post("/admin/prompts/{version_id}/calibrate", response_class=HTMLResponse)
async def admin_calibrate(
    request: Request, version_id: int, clip_ids: list[int] = Form(...)
):
    """Launch a calibration sweep over any number of clips (≥1): for each
    resolution × 2 repeats, run the eligible clips telemetry-only
    (record_only). HIGH applies only to image clips — a resolution with no
    eligible clip is skipped (e.g. an all-video selection → low+medium only,
    4 jobs, no high). Needs the live Gemini API; 503 when offline."""
    require_role(request, "admin")
    core = get_core_ctx(request)
    live = request.app.state.live_ctx
    if live is None:
        raise HTTPException(503, "Gemini offline — calibration needs the live API")
    if not clip_ids:
        raise HTTPException(422, "calibration needs at least one clip")
    try:
        await core.prompts_repo.get_version(core.db, version_id)
    except LookupError:
        raise HTTPException(404, "prompt version not found") from None
    kinds = await media_kinds_for_clip_ids(
        core.db,
        clip_cache_repo=core.clip_cache_repo,
        provider_id=core.settings.archive_provider,
        clip_ids=clip_ids,
    )
    run_group = f"calibration:{version_id}:{int(_time.time())}"
    jobs_created = 0
    for res in CALIBRATION_RESOLUTIONS:
        eligible = [
            c
            for c in clip_ids
            if resolution_valid_for_kind(res, kinds.get(c, "video+audio"))
        ]
        if not eligible:
            continue
        for _ in range(CALIBRATION_REPEATS):
            job_id = await core.jobs_repo.create_job(
                core.db,
                prompt_version_id=version_id,
                clip_ids=eligible,
                kind="studio",
                run_group=run_group,
            )
            start_job_in_background(
                core, live, job_id, force_resolution=res, record_only=True
            )
            jobs_created += 1
    resp = await _prompts_response(request, core)
    resp.headers["X-Calibration-Jobs"] = str(jobs_created)
    return resp


@router.post("/admin/prompts/{version_id}/calibrate/estimate")
async def admin_calibrate_estimate(
    request: Request, version_id: int, body: dict = Body(...)
):
    """Advisory projected cost for a calibration sweep. CoreCtx only —
    fully offline-capable; failures must never block the launch."""
    require_role(request, "admin")
    ctx = get_core_ctx(request)
    clip_ids = [int(c) for c in body.get("clip_ids", [])]
    if not clip_ids:
        return {"projected_cost_usd": None, "runs": 0}
    try:
        est = await estimate_for_clip_ids(
            ctx.db,
            clip_cache_repo=ctx.clip_cache_repo,
            run_telemetry_repo=ctx.run_telemetry_repo,
            prompts_repo=ctx.prompts_repo,
            model_config_repo=ctx.model_config_repo,
            provider_id=ctx.settings.archive_provider,
            clip_ids=clip_ids,
            prompt_version_id=version_id,
        )
    except LookupError:
        raise HTTPException(404, "prompt version not found") from None
    kinds = await media_kinds_for_clip_ids(
        ctx.db,
        clip_cache_repo=ctx.clip_cache_repo,
        provider_id=ctx.settings.archive_provider,
        clip_ids=clip_ids,
    )
    total_runs = 0
    for res in CALIBRATION_RESOLUTIONS:
        eligible = sum(
            1
            for c in clip_ids
            if resolution_valid_for_kind(res, kinds.get(c, "video+audio"))
        )
        total_runs += eligible * CALIBRATION_REPEATS
    p50 = est["cost_usd_p50"]
    n = len(clip_ids)
    # p50 is the total for all clip_ids at the prompt's effective resolution;
    # approximate per-clip cost = p50 / n, scaled by the real run count.
    projected = (p50 / n * total_runs) if (p50 is not None and n) else None
    return {
        "projected_cost_usd": projected,
        "runs": total_runs,
        "pricing_missing": est["pricing_missing"],
    }


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
