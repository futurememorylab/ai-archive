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

    # Actual billable spend per batch: one batched aggregate over every
    # member job_id, summed back per batch_key (no per-batch query).
    cost_by_job = await ctx.run_telemetry_repo.cost_sums_by_job(ctx.db, all_job_ids)
    # Priced vs total run counts per job — when a batch has un-priced (NULL
    # cost) runs, its summed cost is a partial subtotal; flag it rather than
    # present the subtotal as complete (M2).
    counts_by_job = await ctx.run_telemetry_repo.cost_counts_by_job(ctx.db, all_job_ids)
    for v in views:
        spent = sum(cost_by_job.get(jid, 0.0) for jid in v["job_ids"])
        v["cost_usd"] = spent if spent else None
        priced = sum(counts_by_job.get(jid, (0, 0))[0] for jid in v["job_ids"])
        total = sum(counts_by_job.get(jid, (0, 0))[1] for jid in v["job_ids"])
        v["cost_partial"] = total > 0 and priced < total

    est_by_job = await ctx.run_telemetry_repo.est_cost_sums_by_job(ctx.db, all_job_ids)
    for v in views:
        est = sum(est_by_job.get(jid, 0.0) for jid in v["job_ids"])
        v["est_cost_usd"] = est if est else None

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
    kind: str | None = None,
):
    """Server-paginated clip rows for the New-batch picker modal AND the
    Studio archive-picker modal (the shared pickable-clip-list renderer —
    see docs/specs/2026-06-04-studio-archive-picker-reuse-design.md). Lists
    the CatDV catalog, so it needs live services (typed 503 offline).
    Selection is tracked client-side; this renders one page of rows.

    ``kind`` ("image" | "video" | "any"/None) restricts to one media kind —
    used by the calibration picker, which auto-filters to the prompt's
    media_kind. Omitted by the Batches / Studio pickers (unchanged)."""
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
            kind=kind,
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


@router.get("/batches/review-queue")
async def batches_review_queue(request: Request, job_ids: str = ""):
    """Ordered pending clip ids for a batch's jobs — seeds the clip-detail
    review walk. Pure DB (offline-safe)."""
    ctx = get_core_ctx(request)
    ids = [int(x) for x in job_ids.split(",") if x.strip().isdigit()]
    clip_ids = await ctx.review_items_repo.pending_clip_ids_for_jobs(ctx.db, ids)
    return {"clip_ids": clip_ids}


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
        failed = [
            it for it in items
            if it.status == "error" and (only is None or it.catdv_clip_id in only)
        ]
        if not failed:
            continue
        # Flip the targeted failures back to 'pending' synchronously, BEFORE the
        # async job starts. The background run can take seconds to begin flipping
        # statuses (proxy resolution over the VPN), so without this the next
        # /batches/table refresh lands in that gap and shows a stale "Failed"
        # until something else triggers a re-render. 'pending' makes in_flight > 0
        # immediately → the batch reads as running. run_job re-processes
        # 'pending'/'error' items alike, so what actually runs is unchanged.
        for it in failed:
            await core.jobs_repo.update_item_status(core.db, it.id, "pending")
        start_job_in_background(core, live, jid, only_clip_ids=only)
        started.append(jid)
    return {"started": started}
