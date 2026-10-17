"""Batches hub — a dedicated overview of annotation runs (jobs grouped by
run_group). Read path is pure DB (offline-safe, get_core_ctx); retry needs
live services (get_live_ctx → typed 503 offline)."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from backend.app.archive.errors import ProviderError
from backend.app.auth.guards import require_permission
from backend.app.deps import get_core_ctx, get_live_ctx
from backend.app.routes.pages.clips import query_clip_page
from backend.app.routes.pages.templates import templates
from backend.app.services.active_catalog import active_catalog_id
from backend.app.services.batch_names import clean_batch_name
from backend.app.services.clip_list_filters import normalize_anno, normalize_cache
from backend.app.ui.view_models import batch_clip_list, batch_view

router = APIRouter(tags=["batches"])


# Up to this many clips show in full in a row's thumbnail stack; past it, the
# first STACK_SHOWN_WHEN_CAPPED and a "+N" tile of the same size.
STACK_FULL = 6
STACK_SHOWN_WHEN_CAPPED = 5

# The page's three sections, in order (docs/specs/2026-10-17-batches-redesign-design.md).
_SECTIONS = (
    ("attention", "To review", "Review the AI's drafts, or retry clips that failed."),
    ("running", "Running", "Finished clips can be reviewed while the rest run."),
    ("done", "Done", "What you accepted is in the archive."),
)


def _with_preview(view: dict, preview: dict | None) -> None:
    ids = (preview or {}).get("clip_ids", [])
    clips = (preview or {}).get("clips", view["ran"])
    shown = ids if clips <= STACK_FULL else ids[:STACK_SHOWN_WHEN_CAPPED]
    view["clips"] = clips
    view["thumbs"] = [{"id": cid, "url": f"/api/media/{cid}/thumb"} for cid in shown]
    view["more"] = clips - len(shown) if clips > STACK_FULL else 0


async def _load_batches_ctx(ctx, limit: int) -> dict:
    rows = await ctx.jobs_repo.list_batches(ctx.db, limit=limit)
    views = [batch_view(r) for r in rows]
    all_job_ids = [jid for r in rows for jid in r["job_ids"]]

    previews = await ctx.jobs_repo.batch_previews(
        ctx.db, [v["batch_key"] for v in views], first=STACK_FULL
    )
    for v in views:
        _with_preview(v, previews.get(v["batch_key"]))

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

    sections = [
        {"key": key, "title": title, "hint": hint, "batches": members}
        for key, title, hint in _SECTIONS
        if (members := [v for v in views if v["section"] == key])
    ]
    return {"batches": views, "sections": sections}


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


# Clips listed under an expanded batch row; longer batches link to the search.
BATCH_CLIPS_LIMIT = 50


@router.get("/batches/clips", response_class=HTMLResponse)
async def batch_clips(request: Request, job_ids: str = ""):
    """Show clips: every clip of one batch with its status, in the shared
    search-page clip table. Pure DB (offline-safe)."""
    ctx = get_core_ctx(request)
    ids = sorted(int(x) for x in job_ids.split(",") if x.strip().isdigit())
    rows = await ctx.jobs_repo.batch_clip_rows(ctx.db, ids)
    clips, total = batch_clip_list(rows, limit=BATCH_CLIPS_LIMIT)
    return templates.TemplateResponse(
        request,
        "pages/_batch_clips.html",
        {
            "clips": clips,
            "total": total,
            "files_href": f"/?batch={','.join(str(i) for i in ids)}",
        },
    )


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
    catalog_id = active_catalog_id(request, ctx.settings)
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


class RenameBatch(BaseModel):
    job_ids: list[int]
    name: str


@router.post("/batches/rename")
async def rename_batch(request: Request, body: RenameBatch):
    """Rename a batch (every job sharing its run_group). Pure DB."""
    require_permission(request, "run")
    name = clean_batch_name(body.name)
    if name is None:
        raise HTTPException(422, "A batch needs a name.")
    ctx = get_core_ctx(request)
    await ctx.jobs_repo.rename_batch(ctx.db, body.job_ids, name)
    return {"name": name}


class RetryFailed(BaseModel):
    job_ids: list[int]
    clip_ids: list[int] | None = None


async def _requeue(live, job_ids: list[int], status: str, only: set[int] | None) -> list[int]:
    """Flip each job's items in `status` (optionally only `only` clips) back to
    'pending', and the job with them, so the lifespan JobRunner re-claims it.
    The worker processes ONLY 'pending' items, so this is what scopes the run.

    The reset is synchronous, BEFORE the worker picks the job up: the run can
    take seconds to start flipping statuses (proxy resolution over the VPN),
    and 'pending' makes in_flight > 0 at once, so the next /batches/table
    refresh already reads the batch as running instead of a stale state.
    Routes never execute jobs themselves (ADR 0125)."""
    if live.proxy_resolver is None:
        raise HTTPException(503, "Proxy resolver offline — cannot run annotations")
    core = live.core
    started: list[int] = []
    for jid in job_ids:
        items = await core.jobs_repo.list_items(core.db, jid)
        targets = [
            it for it in items if it.status == status and (only is None or it.catdv_clip_id in only)
        ]
        if not targets:
            continue
        for it in targets:
            await core.jobs_repo.update_item_status(core.db, it.id, "pending")
        await core.jobs_repo.update_status(core.db, jid, "pending")
        started.append(jid)
    return started


@router.post("/batches/retry-failed")
async def retry_failed(request: Request, body: RetryFailed):
    """Re-run failed clips; untargeted failures in the same job stay as they
    are. Requires live services + a proxy resolver."""
    live = get_live_ctx(request)  # 503 when offline
    only = set(body.clip_ids) if body.clip_ids else None
    return {"started": await _requeue(live, body.job_ids, "error", only)}


class RestartBatch(BaseModel):
    job_ids: list[int]


@router.post("/batches/restart")
async def restart_batch(request: Request, body: RestartBatch):
    """Run the clips a Cancel stopped. Clips that already ran keep their
    results. Requires live services + a proxy resolver."""
    require_permission(request, "run")
    live = get_live_ctx(request)  # 503 when offline
    return {"started": await _requeue(live, body.job_ids, "cancelled", None)}
