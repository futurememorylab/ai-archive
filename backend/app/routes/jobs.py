"""Jobs routes — HTTP endpoints under /api/jobs for creating and
inspecting annotation jobs. Delegates execution to the annotator service."""

import asyncio
import contextlib

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from backend.app.auth.guards import require_permission
from backend.app.deps import get_core_ctx
from backend.app.routes.events import _event_generator
from backend.app.services.annotator import JOBS_TOPIC, run_job
from backend.app.services.run_estimator import estimate_for_clip_ids

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class JobCreate(BaseModel):
    prompt_version_id: int
    clip_ids: list[int]
    auto_start: bool = True
    # Shared token tying together the per-kind jobs of one bulk action so the
    # Batch filter can present them as a single run.
    run_group: str | None = None


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_job(request: Request, body: JobCreate, background: BackgroundTasks):
    require_permission(request, "run")
    ctx = get_core_ctx(request)
    job_id = await ctx.jobs_repo.create_job(
        ctx.db,
        prompt_version_id=body.prompt_version_id,
        clip_ids=body.clip_ids,
        run_group=body.run_group,
    )
    # Auto-start requires every live service the annotator needs. When the
    # app is offline (no LiveCtx) or the resolver is fs-only (None), the job
    # is created but left for a later run.
    live = request.app.state.live_ctx
    started = bool(body.auto_start and live is not None and live.proxy_resolver is not None)
    if started:
        start_job_in_background(ctx, live, job_id)
    return {"id": job_id, "started": started}


async def _run_in_bg(ctx, job_id: int, *, only_clip_ids: set[int] | None = None) -> None:
    try:
        await run_job(
            db=ctx.db,
            job_id=job_id,
            archive=ctx.archive,
            proxy_resolver=ctx.proxy_resolver,
            ai_store=ctx.ai_store,
            gemini=ctx.gemini,
            event_bus=ctx.event_bus,
            annotations_repo=ctx.annotations_repo,
            review_items_repo=ctx.review_items_repo,
            jobs_repo=ctx.jobs_repo,
            prompts_repo=ctx.prompts_repo,
            studio_runs_repo=ctx.studio_runs_repo,
            uploaded_clips_repo=ctx.uploaded_clips_repo,
            run_telemetry_repo=ctx.run_telemetry_repo,
            telemetry_ctx=ctx.telemetry_ctx,
            prefetch_queue_repo=ctx.prefetch_queue_repo,
            only_clip_ids=only_clip_ids,
        )
    except asyncio.CancelledError:
        # Cancelled mid-flight (cancel route or shutdown drain): an item may
        # have advanced into a transient state after the cancel sweep, so
        # reconcile before propagating, leaving nothing 'prompting' forever.
        with contextlib.suppress(Exception):
            await ctx.jobs_repo.cancel_job(ctx.db, job_id)
        raise
    finally:
        ctx._running_jobs.pop(job_id, None)


def start_job_in_background(
    core, live, job_id: int, *, only_clip_ids: set[int] | None = None
) -> None:
    """Spawn run_job for `job_id` as a tracked background task. Shared by
    POST /api/jobs (auto-start) and the Batches retry-failed route."""
    task = asyncio.create_task(_run_in_bg(live, job_id, only_clip_ids=only_clip_ids))
    core._running_jobs[job_id] = task


@router.get("")
async def list_jobs(request: Request, limit: int = 50):
    ctx = get_core_ctx(request)
    return [j.model_dump() for j in await ctx.jobs_repo.list_jobs(ctx.db, limit=limit)]


@router.get("/active")
async def list_active_jobs(request: Request):
    """Running jobs with progress counts — powers the topbar indicator."""
    ctx = get_core_ctx(request)
    out = []
    for job in await ctx.jobs_repo.list_running(ctx.db):
        done, total, errors = await ctx.jobs_repo.progress(ctx.db, job.id)
        out.append(
            {
                "id": job.id,
                "kind": job.kind,
                "status": job.status,
                "done": done,
                "total": total,
                "errors": errors,
                "phases": await ctx.jobs_repo.phase_counts(ctx.db, job.id),
            }
        )
    return out


@router.get("/active-for-clip/{clip_id}")
async def active_job_for_clip(request: Request, clip_id: int):
    """The running job (if any) touching this clip, as {job_id, item_status},
    or {}. Lets the clip page resume the annotate button after a reload.

    Registered before /{job_id} so it isn't shadowed by the catch-all."""
    ctx = get_core_ctx(request)
    found = await ctx.jobs_repo.find_running_item_for_clip(ctx.db, clip_id)
    return found or {}


@router.get("/events")
async def jobs_events(request: Request):
    """SSE stream of the global `jobs` topic — powers the topbar indicator."""
    ctx = get_core_ctx(request)

    async def stream():
        async for frame in _event_generator(ctx.event_bus, topic=JOBS_TOPIC):
            if await request.is_disconnected():
                return
            yield {"data": frame.removeprefix("data: ").rstrip("\n")}

    return EventSourceResponse(stream())


class EstimateRequest(BaseModel):
    prompt_version_id: int
    clip_ids: list[int] = Field(max_length=2000)


@router.post("/estimate")
async def estimate_job(request: Request, body: EstimateRequest):
    """Pre-run cost estimate. CoreCtx only — fully offline-capable.
    Advisory: failures here must never block launching a run (the UI
    treats errors as 'no estimate shown')."""
    ctx = get_core_ctx(request)
    try:
        return await estimate_for_clip_ids(
            ctx.db,
            clip_cache_repo=ctx.clip_cache_repo,
            run_telemetry_repo=ctx.run_telemetry_repo,
            prompts_repo=ctx.prompts_repo,
            provider_id=ctx.settings.archive_provider,
            clip_ids=body.clip_ids,
            prompt_version_id=body.prompt_version_id,
        )
    except LookupError:
        raise HTTPException(404, "prompt version not found") from None


@router.get("/{job_id}")
async def get_job(request: Request, job_id: int):
    ctx = get_core_ctx(request)
    try:
        job = await ctx.jobs_repo.get_job(ctx.db, job_id)
    except LookupError:
        raise HTTPException(404, "job not found") from None
    items = await ctx.jobs_repo.list_items(ctx.db, job_id)
    return {**job.model_dump(), "items": [it.model_dump() for it in items]}


@router.post("/{job_id}/cancel")
async def cancel_job(request: Request, job_id: int):
    ctx = get_core_ctx(request)
    # Reconcile DB state first (job + in-flight items → cancelled), then
    # interrupt the in-flight task so cancel is prompt instead of waiting out
    # the current clip's long Gemini call. The task's CancelledError handler
    # re-runs cancel_job to mop up any item that raced into a transient state.
    await ctx.jobs_repo.cancel_job(ctx.db, job_id)
    task = ctx._running_jobs.get(job_id)
    if isinstance(task, asyncio.Task):
        task.cancel()
    return {"id": job_id, "status": "cancelled"}
