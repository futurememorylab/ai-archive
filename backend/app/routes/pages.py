import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from backend.app.archive.errors import ProviderError
from backend.app.archive.model import ClipQuery
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
):
    ctx = request.app.state.ctx
    if ctx.archive is None:
        raise HTTPException(503, "archive provider not initialized")

    catalog_id = str(ctx.settings.catdv_catalog_id)

    # `?refresh=1` lets the user bypass the list cache when they suspect
    # upstream changed. Wipe every cached page for this catalog so the next
    # list_clips() hits CatDV and we don't serve a stale neighbour page.
    if refresh:
        await ctx.clip_list_cache_repo.invalidate_catalog(
            ctx.db, provider_id="catdv", catalog_id=catalog_id
        )

    try:
        page = await ctx.archive.list_clips(
            catalog_id,
            ClipQuery(text=q, offset=offset, limit=limit),
        )
    except ProviderError as exc:
        raise HTTPException(502, f"archive error: {exc}") from exc

    # After list_clips, the adapter has either served from cache or
    # written-through; either way the row's fetched_at is the age of the
    # data the user is looking at.
    cache_entry = await ctx.clip_list_cache_repo.get(
        ctx.db,
        provider_id="catdv",
        catalog_id=catalog_id,
        query_text=q,
        offset=offset,
        limit=limit,
    )
    cache_fetched_at = (
        cache_entry["fetched_at"] if cache_entry is not None else None
    )

    # Bulk cache lookup so each row gets a badge with no per-row HTMX hop.
    statuses: dict[tuple[str, str], object] = {}
    if ctx.cache_inspector is not None and page.items:
        keys = [c.key for c in page.items]
        rows = await ctx.cache_inspector.status_for_clips(keys)
        statuses = {r.clip_key: r for r in rows}

    ctx_dict = {
        "q": q or "",
        "offset": offset,
        "limit": limit,
        "total": page.total,
        "catalog": {
            "id": ctx.settings.catdv_catalog_id,
            "name": "AI katalog",
        },
        "clips": [
            clip_summary(c, cache_status=statuses.get(c.key))
            for c in page.items
        ],
        "prev_offset": max(0, offset - limit) if offset > 0 else None,
        "next_offset": offset + limit if offset + limit < page.total else None,
        "cache_fetched_at": cache_fetched_at,
        "cache_age": _humanize_age(cache_fetched_at),
    }

    template = (
        "pages/_clips_tbody.html"
        if request.headers.get("HX-Request") == "true"
        else "pages/clips.html"
    )
    return templates.TemplateResponse(request, template, ctx_dict)


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
    return templates.TemplateResponse(request, "pages/clip_detail.html", ctx_dict)


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


@router.get("/prompts/{prompt_id}", response_class=HTMLResponse)
async def prompt_detail_page(request: Request, prompt_id: int, version_id: int | None = None):
    ctx = request.app.state.ctx
    repo = ctx.prompts_repo
    try:
        selected, versions = await repo.get_with_versions(ctx.db, prompt_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    prompts = await repo.list_active(ctx.db)
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
            "archived_view": False,
            "rail_active": "prompts",
        },
    )


@router.post("/prompts/{prompt_id}/_new_version")
async def action_new_version(request: Request, prompt_id: int):
    ctx = request.app.state.ctx
    new_vid = await ctx.prompts_repo.create_version(ctx.db, prompt_id)
    return RedirectResponse(
        f"/prompts/{prompt_id}?version_id={new_vid}", status_code=303
    )


@router.post("/prompts/{prompt_id}/versions/{version_id}/_promote")
async def action_promote_version(request: Request, prompt_id: int, version_id: int):
    ctx = request.app.state.ctx
    await ctx.prompts_repo.promote_version(ctx.db, prompt_id, version_id)
    return RedirectResponse(
        f"/prompts/{prompt_id}?version_id={version_id}", status_code=303
    )


@router.post("/prompts/{prompt_id}/_duplicate")
async def action_duplicate_prompt(request: Request, prompt_id: int):
    ctx = request.app.state.ctx
    new_pid, _ = await ctx.prompts_repo.duplicate(ctx.db, prompt_id)
    return RedirectResponse(f"/prompts/{new_pid}", status_code=303)


@router.post("/prompts/{prompt_id}/_archive")
async def action_archive_prompt(request: Request, prompt_id: int):
    ctx = request.app.state.ctx
    await ctx.prompts_repo.archive(ctx.db, prompt_id)
    return RedirectResponse("/prompts", status_code=303)


@router.post("/prompts/{prompt_id}/_restore")
async def action_restore_prompt(request: Request, prompt_id: int):
    ctx = request.app.state.ctx
    await ctx.prompts_repo.restore(ctx.db, prompt_id)
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
