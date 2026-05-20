from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
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
