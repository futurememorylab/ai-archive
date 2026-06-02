"""Batches hub — a dedicated overview of annotation runs (jobs grouped by
run_group). Read path is pure DB (offline-safe, get_core_ctx); retry needs
live services (get_live_ctx → typed 503 offline)."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from backend.app.deps import get_core_ctx, get_live_ctx
from backend.app.routes.jobs import start_job_in_background
from backend.app.routes.pages.templates import templates
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
