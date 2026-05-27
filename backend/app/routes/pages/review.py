"""Consolidated draft-review page (/review): lists clips with un-applied
review items, with batch (job) and media-type filters. Mirrors the cache
page's full-page / HTMX-partial split."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from backend.app.deps import get_ctx
from backend.app.routes.pages.templates import templates
from backend.app.ui.pagination import page_offsets
from backend.app.ui.view_models import _media_kind

router = APIRouter(tags=["pages"])


def _counts_label(row: dict) -> str:
    parts = []
    if row["marker_count"]:
        parts.append(f'{row["marker_count"]} markers')
    if row["field_count"]:
        parts.append(f'{row["field_count"]} fields')
    if row["note_count"]:
        parts.append(f'{row["note_count"]} notes')
    return " · ".join(parts) or "—"


@router.get("/review", response_class=HTMLResponse)
async def review_page(
    request: Request,
    job_id: int | None = None,
    media: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> HTMLResponse:
    ctx = get_ctx(request)
    is_htmx = request.headers.get("HX-Request") == "true"

    pending = await ctx.review_items_repo.list_pending_clips(
        ctx.db, job_id=job_id, limit=limit, offset=offset
    )
    total = await ctx.review_items_repo.count_pending_clips(ctx.db, job_id=job_id)

    rows = []
    for p in pending:
        clip_id = p["catdv_clip_id"]
        clip = await ctx.clip_cache_repo.get_by_key(
            ctx.db,
            provider_id="catdv",
            provider_clip_id=str(clip_id),
        )
        kind = _media_kind(clip.provider_data) if clip is not None else "video"
        if media in ("video", "image") and kind != media:
            continue
        rows.append({
            "select_value": f"catdv/{clip_id}",
            "catdv_clip_id": clip_id,
            "cache": None,
            "thumb_url": f"/api/media/{clip_id}/thumb",
            "name": p["catdv_clip_name"],
            "name_sub": None,
            "row_href": f"/clips/{clip_id}?review=1",
            "row_class": None,
            "row_bytes": None,
            "kind": kind,
            "counts_label": _counts_label(p),
            "created_at": p["created_at"],
        })

    jobs = await ctx.jobs_repo.list_jobs(ctx.db, limit=50)
    metric = {
        "clips": total,
        "markers": sum(p["marker_count"] for p in pending),
        "fields": sum(p["field_count"] for p in pending),
        "notes": sum(p["note_count"] for p in pending),
    }
    prev_offset, next_offset = page_offsets(offset, limit, total)

    ctx_dict = {
        "rows": rows,
        "total": total,
        "offset": offset,
        "limit": limit,
        "prev_offset": prev_offset,
        "next_offset": next_offset,
        "filters": {"job_id": job_id, "media": media or ""},
        "jobs": jobs,
        "metric": metric,
    }
    if is_htmx:
        return templates.TemplateResponse(request, "pages/_review_table.html", ctx_dict)
    return templates.TemplateResponse(request, "pages/review.html", ctx_dict)
