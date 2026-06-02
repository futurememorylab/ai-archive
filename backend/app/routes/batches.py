"""Batches hub — a dedicated overview of annotation runs (jobs grouped by
run_group). Read path is pure DB (offline-safe, get_core_ctx); retry needs
live services (get_live_ctx → typed 503 offline)."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from backend.app.archive.errors import ProviderError
from backend.app.deps import get_core_ctx, get_live_ctx
from backend.app.routes.jobs import start_job_in_background
from backend.app.routes.pages.clips import query_clip_page
from backend.app.routes.pages.templates import templates
from backend.app.services.clip_list_filters import normalize_anno, normalize_cache
from backend.app.ui.view_models import batch_view

router = APIRouter(tags=["batches"])


async def _load_batches_ctx(ctx, limit: int) -> dict:
    rows = await ctx.jobs_repo.list_batches(ctx.db, limit=limit)
    views = [batch_view(r) for r in rows]

    all_job_ids = [jid for r in rows for jid in r["job_ids"]]
    fails = await ctx.jobs_repo.failed_items_for_jobs(ctx.db, all_job_ids)
    job_to_key = {jid: r["batch_key"] for r in rows for jid in r["job_ids"]}
    fails_by_key: dict[str, list[dict]] = {}
    for f in fails:
        key = job_to_key.get(f["job_id"])
        fails_by_key.setdefault(key, []).append(
            {
                "id": f["catdv_clip_id"],
                "name": f["clip_name"] or f"Clip {f['catdv_clip_id']}",
                "error": f["error_message"] or "Unknown error",
            }
        )
    for v in views:
        v["fails"] = fails_by_key.get(v["batch_key"], [])

    total_batches = await ctx.jobs_repo.count_total_batches(ctx.db)
    metrics = {
        "total_batches": total_batches,
        "shown": len(views),
        "drafts_produced": sum(v["completed"] for v in views),
        "awaiting_review": sum(v["awaiting"] for v in views),
        "awaiting_batches": sum(1 for v in views if not v["running"] and v["awaiting"] > 0),
        "failed_clips": sum(v["failed"] for v in views),
    }
    return {"batches": views, "metrics": metrics}


@router.get("/batches", response_class=HTMLResponse)
async def batches_page(request: Request, limit: int = 50):
    ctx = get_core_ctx(request)
    ctx_dict = await _load_batches_ctx(ctx, limit)
    return templates.TemplateResponse(request, "pages/batches.html", ctx_dict)


@router.get("/batches/table", response_class=HTMLResponse)
async def batches_table(request: Request, limit: int = 50):
    """HTMX/fetch partial — the table region only, for live refresh."""
    ctx = get_core_ctx(request)
    ctx_dict = await _load_batches_ctx(ctx, limit)
    return templates.TemplateResponse(request, "pages/_batches_table.html", ctx_dict)


@router.get("/batches/picker", response_class=HTMLResponse)
async def batches_picker(
    request: Request,
    q: str | None = None,
    cache: str | None = None,
    anno: str | None = None,
    offset: int = 0,
    limit: int = 12,
):
    """Server-paginated clip rows for the New-batch picker modal. Lists the
    CatDV catalog, so it needs live services (typed 503 offline). Selection
    is tracked client-side; this only renders one page of candidate rows."""
    ctx = get_live_ctx(request)
    catalog_id = str(ctx.settings.catdv_catalog_id)
    host_local = getattr(getattr(ctx, "proxy_resolver", None), "is_host_local", False)
    try:
        rows, total, _ = await query_clip_page(
            ctx,
            catalog_id=catalog_id,
            q=q,
            offset=offset,
            limit=limit,
            cache_f=normalize_cache(cache),
            anno_f=normalize_anno(anno),
            batch_ids=[],
            host_local_proxies=host_local,
        )
    except ProviderError as exc:
        raise HTTPException(502, f"archive error: {exc}") from exc
    return templates.TemplateResponse(
        request,
        "pages/_batch_picker.html",
        {
            "rows": rows,
            "total": total,
            "offset": offset,
            "limit": limit,
            "head_cells": "pages/_batch_picker_head.html",
            "row_cells": "pages/_batch_picker_cells.html",
            "cache_label": "Cache",
            "colspan": 6,
            "empty_msg": "No clips match.",
        },
    )


class RetryFailed(BaseModel):
    job_ids: list[int]
    clip_ids: list[int] | None = None


@router.post("/batches/retry-failed")
async def retry_failed(request: Request, body: RetryFailed):
    """Re-run failed clips. Reuses annotator.run_job (which only re-processes
    'error'/'pending' items); only_clip_ids narrows to a single clip when
    given. Requires live services + a proxy resolver."""
    live = get_live_ctx(request)  # 503 when offline
    if live.proxy_resolver is None:
        raise HTTPException(503, "Proxy resolver offline — cannot run annotations")
    core = live.core
    only = set(body.clip_ids) if body.clip_ids else None

    started: list[int] = []
    for jid in body.job_ids:
        items = await core.jobs_repo.list_items(core.db, jid)
        has_failed = any(
            it.status == "error" and (only is None or it.catdv_clip_id in only)
            for it in items
        )
        if not has_failed:
            continue
        start_job_in_background(core, live, jid, only_clip_ids=only)
        started.append(jid)
    return {"started": started}
