import json
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from backend.app.archive.errors import ProviderError
from backend.app.archive.model import CanonicalClip, ClipQuery
from backend.app.models.prompt import TargetMap
from backend.app.repositories.prompts import VersionImmutableError
from backend.app.services.clip_list_filters import (
    is_active as filters_active,
    normalize_anno,
    normalize_cache,
    resolve as resolve_filters,
)
from backend.app.ui.view_models import clip_detail, clip_summary


def _humanize_age(fetched_at_iso: str | None) -> str | None:
    if not fetched_at_iso:
        return None
    try:
        ts = datetime.fromisoformat(fetched_at_iso)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - ts
    secs = int(delta.total_seconds())
    if secs < 5:
        return "just now"
    if secs < 60:
        return f"{secs}s ago"
    mins = secs // 60
    if mins < 60:
        return f"{mins}m ago"
    hours = mins // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
from backend.app.timecode import secs_to_smpte  # noqa: E402

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["smpte"] = secs_to_smpte

router = APIRouter(tags=["pages"])


@router.get("/", response_class=HTMLResponse)
async def clips_list(
    request: Request,
    q: str | None = None,
    offset: int = 0,
    limit: int = 50,
    refresh: int = 0,
    cache: str | None = None,
    anno: str | None = None,
):
    ctx = request.app.state.ctx
    if ctx.archive is None:
        raise HTTPException(503, "archive provider not initialized")

    catalog_id = str(ctx.settings.catdv_catalog_id)
    cache_f = normalize_cache(cache)
    anno_f = normalize_anno(anno)

    # `?refresh=1` lets the user bypass the list cache when they suspect
    # upstream changed. Wipe every cached page for this catalog so the next
    # list_clips() hits CatDV and we don't serve a stale neighbour page.
    if refresh:
        await ctx.clip_list_cache_repo.invalidate_catalog(
            ctx.db, provider_id="catdv", catalog_id=catalog_id
        )

    cache_fetched_at: str | None = None

    try:
        if filters_active(cache_f, anno_f):
            clips, total = await _filtered_page(
                ctx,
                catalog_id=catalog_id,
                q=q,
                offset=offset,
                limit=limit,
                cache_filter=cache_f,
                anno_filter=anno_f,
            )
        else:
            page = await ctx.archive.list_clips(
                catalog_id,
                ClipQuery(text=q, offset=offset, limit=limit),
            )
            clips = list(page.items)
            total = page.total
            entry = await ctx.clip_list_cache_repo.get(
                ctx.db,
                provider_id="catdv",
                catalog_id=catalog_id,
                query_text=q,
                offset=offset,
                limit=limit,
            )
            cache_fetched_at = entry["fetched_at"] if entry is not None else None
    except ProviderError as exc:
        raise HTTPException(502, f"archive error: {exc}") from exc

    # Bulk cache lookup so each row gets a badge with no per-row HTMX hop.
    statuses: dict[tuple[str, str], object] = {}
    if ctx.cache_inspector is not None and clips:
        keys = [c.key for c in clips]
        rows = await ctx.cache_inspector.status_for_clips(keys)
        statuses = {r.clip_key: r for r in rows}

    ctx_dict = {
        "q": q or "",
        "offset": offset,
        "limit": limit,
        "total": total,
        "cache_filter": cache_f,
        "anno_filter": anno_f,
        "filters_active": filters_active(cache_f, anno_f),
        "catalog": {
            "id": ctx.settings.catdv_catalog_id,
            "name": "AI katalog",
        },
        "clips": [
            clip_summary(c, cache_status=statuses.get(c.key))
            for c in clips
        ],
        "prev_offset": max(0, offset - limit) if offset > 0 else None,
        "next_offset": offset + limit if offset + limit < total else None,
        "cache_fetched_at": cache_fetched_at,
        "cache_age": _humanize_age(cache_fetched_at),
    }

    template = (
        "pages/_clips_tbody.html"
        if request.headers.get("HX-Request") == "true"
        else "pages/clips.html"
    )
    return templates.TemplateResponse(request, template, ctx_dict)


async def _filtered_page(
    ctx,
    *,
    catalog_id: str,
    q: str | None,
    offset: int,
    limit: int,
    cache_filter,
    anno_filter,
) -> tuple[list[CanonicalClip], int]:
    """Local-first paginated list when any filter is active.

    Builds the candidate clip-id set from SQLite, hydrates each clip
    (preferring the metadata cache, falling back to a single CatDV fetch),
    optionally applies the text query, sorts by name for stable paging,
    then slices to the requested page.
    """
    candidate_ids = await resolve_filters(
        ctx.db,
        provider_id="catdv",
        catalog_id=catalog_id,
        cache=cache_filter,
        anno=anno_filter,
    )
    if not candidate_ids:
        return [], 0

    needle = (q or "").strip().casefold() or None

    hydrated: list[CanonicalClip] = []
    for cid in candidate_ids:
        clip = await _hydrate_clip(ctx, cid)
        if clip is None:
            continue
        if needle is not None and needle not in clip.name.casefold():
            continue
        hydrated.append(clip)

    hydrated.sort(key=lambda c: (c.name.casefold(), int(c.key[1])))
    total = len(hydrated)
    return hydrated[offset : offset + limit], total


async def _hydrate_clip(ctx, clip_id: int) -> CanonicalClip | None:
    """Fetch a CanonicalClip cheaply, preferring local metadata cache."""
    clip = await ctx.clip_cache_repo.get_by_key(
        ctx.db,
        provider_id="catdv",
        provider_clip_id=str(clip_id),
    )
    if clip is not None:
        return clip
    try:
        return await ctx.archive.get_clip(str(clip_id))
    except ProviderError:
        # Stale ID (e.g. local cache row whose upstream clip was removed)
        # — skip silently so one orphan doesn't blow up the whole page.
        return None


async def _build_draft_for_clip(ctx, clip_id: int) -> dict:
    from backend.app.services.draft_view import build_draft_view

    annotations = await ctx.annotations_repo.list_by_clip(ctx.db, clip_id)
    if not annotations:
        return build_draft_view(annotation=None, review_items=[])
    latest = annotations[0]  # DESC order
    all_items = await ctx.review_items_repo.list_by_clip(ctx.db, clip_id)
    items = [it for it in all_items if it.annotation_id == latest.id]
    prompt_name: str | None = None
    version_num: int | None = None
    try:
        version = await ctx.prompts_repo.get_version(ctx.db, latest.prompt_version_id)
        version_num = version.version_num
        prompt, _ = await ctx.prompts_repo.get_with_versions(ctx.db, version.prompt_id)
        prompt_name = prompt.name
    except LookupError:
        pass
    return build_draft_view(
        annotation=latest,
        review_items=items,
        prompt_name=prompt_name,
        version_num=version_num,
        created_at=latest.created_at,
    )


@router.get("/clips/{clip_id}", response_class=HTMLResponse)
async def clip_detail_page(request: Request, clip_id: int):
    ctx = request.app.state.ctx
    if ctx.archive is None:
        raise HTTPException(503, "archive provider not initialized")
    try:
        clip = await ctx.archive.get_clip(str(clip_id))
    except ProviderError as exc:
        raise HTTPException(404, f"clip not found: {exc}") from exc

    cache_status = None
    if ctx.cache_inspector is not None:
        cache_status = await ctx.cache_inspector.status_for_clip(clip.key)

    ctx_dict = clip_detail(clip, cache_status=cache_status)
    ctx_dict["duration_smpte"] = secs_to_smpte(
        ctx_dict["clip"]["duration_secs"], ctx_dict["clip"]["fps"]
    )
    ctx_dict["draft"] = await _build_draft_for_clip(ctx, clip_id)
    return templates.TemplateResponse(request, "pages/clip_detail.html", ctx_dict)


@router.get("/clips/{clip_id}/draft", response_class=HTMLResponse)
async def clip_draft_partial(request: Request, clip_id: int):
    ctx = request.app.state.ctx
    if ctx.archive is None:
        raise HTTPException(503, "archive provider not initialized")
    try:
        # Confirm the clip exists so we 404 properly; we don't render it here.
        await ctx.archive.get_clip(str(clip_id))
    except ProviderError as exc:
        raise HTTPException(404, f"clip not found: {exc}") from exc

    draft = await _build_draft_for_clip(ctx, clip_id)
    return templates.TemplateResponse(
        request, "pages/_anno_draft.html", {"draft": draft, "clip": None}
    )


@router.get("/prompts", response_class=HTMLResponse)
async def prompts_page(request: Request, archived: int = 0):
    ctx = request.app.state.ctx
    repo = ctx.prompts_repo
    prompts = await (repo.list_archived(ctx.db) if archived else repo.list_active(ctx.db))
    selected = None
    selected_version = None
    versions: list = []
    if prompts:
        first_id = prompts[0].id
        selected, versions = await repo.get_with_versions(ctx.db, first_id)
        selected_version = _pick_default_version(versions)
    return templates.TemplateResponse(
        request, "pages/prompts.html",
        {
            "prompts": [p.model_dump() for p in prompts],
            "selected": selected.model_dump() if selected else None,
            "selected_version": _version_view(selected_version) if selected_version else None,
            "versions": [_version_view(v) for v in versions],
            "archived_view": bool(archived),
            "rail_active": "prompts",
        },
    )


@router.get("/prompts/archived", response_class=HTMLResponse)
async def prompts_archived_page(request: Request):
    return await prompts_page(request, archived=1)


@router.get("/prompts/new", response_class=HTMLResponse)
async def prompt_new_page(request: Request):
    ctx = request.app.state.ctx
    prompts = await ctx.prompts_repo.list_active(ctx.db)
    return templates.TemplateResponse(
        request, "pages/_prompt_new.html",
        {
            "prompts": [p.model_dump() for p in prompts],
            "rail_active": "prompts",
            "error": None,
            "form": {"name": "", "description": "", "body": "",
                     "target_map_text": "{}", "output_schema_text": "{}",
                     "model": "gemini-2.5-flash-lite"},
        },
    )


@router.post("/prompts/_create")
async def action_create_prompt(request: Request):
    ctx = request.app.state.ctx
    form = await request.form()
    name = (form.get("name") or "").strip()
    description = (form.get("description") or "").strip() or None
    body = form.get("body") or ""
    target_map_text = form.get("target_map") or "{}"
    output_schema_text = form.get("output_schema") or "{}"
    model = form.get("model") or "gemini-2.5-flash-lite"
    error = None
    target_map = None
    output_schema = None
    try:
        target_map = json.loads(target_map_text)
        output_schema = json.loads(output_schema_text)
    except json.JSONDecodeError as exc:
        error = f"invalid JSON: {exc}"
    if error is None:
        try:
            TargetMap.model_validate(target_map)
        except ValidationError as exc:
            error = f"invalid target_map: {exc.errors()[0]['msg']}"
    if not name:
        error = "name is required"
    if error:
        prompts = await ctx.prompts_repo.list_active(ctx.db)
        return templates.TemplateResponse(
            request, "pages/_prompt_new.html",
            {
                "prompts": [p.model_dump() for p in prompts],
                "rail_active": "prompts",
                "error": error,
                "form": {"name": name, "description": description or "",
                         "body": body, "target_map_text": target_map_text,
                         "output_schema_text": output_schema_text, "model": model},
            },
            status_code=400,
        )
    try:
        pid, _ = await ctx.prompts_repo.create_with_initial_version(
            ctx.db, name=name, description=description,
            body=body, target_map=target_map,
            output_schema=output_schema, model=model,
        )
    except aiosqlite.IntegrityError as exc:
        prompts = await ctx.prompts_repo.list_active(ctx.db)
        return templates.TemplateResponse(
            request, "pages/_prompt_new.html",
            {
                "prompts": [p.model_dump() for p in prompts],
                "rail_active": "prompts",
                "error": f"name already exists: {exc}",
                "form": {"name": name, "description": description or "",
                         "body": body, "target_map_text": target_map_text,
                         "output_schema_text": output_schema_text, "model": model},
            },
            status_code=400,
        )
    return RedirectResponse(f"/prompts/{pid}", status_code=303)


@router.get("/prompts/{prompt_id}", response_class=HTMLResponse)
async def prompt_detail_page(request: Request, prompt_id: int, version_id: int | None = None):
    ctx = request.app.state.ctx
    repo = ctx.prompts_repo
    try:
        selected, versions = await repo.get_with_versions(ctx.db, prompt_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    if selected.archived:
        prompts = await repo.list_archived(ctx.db)
        archived_view = True
    else:
        prompts = await repo.list_active(ctx.db)
        archived_view = False
    selected_version = (
        await repo.get_version(ctx.db, version_id) if version_id is not None
        else _pick_default_version(versions)
    )
    return templates.TemplateResponse(
        request, "pages/prompts.html",
        {
            "prompts": [p.model_dump() for p in prompts],
            "selected": selected.model_dump(),
            "selected_version": _version_view(selected_version),
            "versions": [_version_view(v) for v in versions],
            "archived_view": archived_view,
            "rail_active": "prompts",
        },
    )


@router.post("/prompts/{prompt_id}/_new_version")
async def action_new_version(request: Request, prompt_id: int):
    ctx = request.app.state.ctx
    try:
        new_vid = await ctx.prompts_repo.create_version(ctx.db, prompt_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    return RedirectResponse(
        f"/prompts/{prompt_id}?version_id={new_vid}", status_code=303
    )


@router.post("/prompts/{prompt_id}/versions/{version_id}/_promote")
async def action_promote_version(request: Request, prompt_id: int, version_id: int):
    ctx = request.app.state.ctx
    try:
        v = await ctx.prompts_repo.get_version(ctx.db, version_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    if v.prompt_id != prompt_id:
        raise HTTPException(404, "version does not belong to prompt")
    try:
        await ctx.prompts_repo.promote_version(ctx.db, prompt_id, version_id)
    except VersionImmutableError:
        pass  # silent no-op for page action; promote button only shown for drafts
    return RedirectResponse(
        f"/prompts/{prompt_id}?version_id={version_id}", status_code=303
    )


@router.post("/prompts/{prompt_id}/_duplicate")
async def action_duplicate_prompt(
    request: Request,
    prompt_id: int,
    name: str | None = Form(default=None),
    description: str | None = Form(default=None),
):
    ctx = request.app.state.ctx
    cleaned_name = name.strip() if name is not None else None
    cleaned_desc = description if description is not None else None
    try:
        new_pid, _ = await ctx.prompts_repo.duplicate(
            ctx.db,
            prompt_id,
            name=cleaned_name or None,
            description=cleaned_desc,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except aiosqlite.IntegrityError:
        return JSONResponse(
            status_code=409,
            content={
                "error_code": "name_conflict",
                "message": f"A prompt named {cleaned_name!r} already exists.",
            },
        )
    return RedirectResponse(f"/prompts/{new_pid}", status_code=303)


@router.post("/prompts/{prompt_id}/_archive")
async def action_archive_prompt(request: Request, prompt_id: int):
    ctx = request.app.state.ctx
    try:
        await ctx.prompts_repo.archive(ctx.db, prompt_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    return RedirectResponse("/prompts", status_code=303)


@router.post("/prompts/{prompt_id}/_restore")
async def action_restore_prompt(request: Request, prompt_id: int):
    ctx = request.app.state.ctx
    try:
        await ctx.prompts_repo.restore(ctx.db, prompt_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    return RedirectResponse(f"/prompts/{prompt_id}", status_code=303)


def _pick_default_version(versions: list) -> object | None:
    """Default-displayed version: current production, fallback to latest."""
    for v in versions:
        if v.state == "production":
            return v
    return versions[0] if versions else None


def _version_view(v) -> dict:
    """Renderable dict — JSON fields stringified pretty for the textareas."""
    return {
        "id": v.id,
        "prompt_id": v.prompt_id,
        "version_num": v.version_num,
        "state": v.state,
        "body": v.body,
        "target_map_text": json.dumps(
            v.target_map.model_dump() if hasattr(v.target_map, "model_dump") else v.target_map,
            indent=2, ensure_ascii=False,
        ),
        "output_schema_text": json.dumps(v.output_schema, indent=2, ensure_ascii=False),
        "model": v.model,
        "created_at": v.created_at,
        "updated_at": v.updated_at,
    }
