"""Jobs routes — HTTP endpoints under /api/jobs for creating and
inspecting annotation jobs. Delegates execution to the annotator service."""

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from backend.app.auth.guards import require_permission
from backend.app.deps import get_core_ctx
from backend.app.routes.events import _event_generator
from backend.app.services.annotator import JOBS_TOPIC
from backend.app.services.run_estimator import estimate_for_clip_ids

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class JobCreate(BaseModel):
    prompt_version_id: int
    clip_ids: list[int]
    # Shared token tying together the per-kind jobs of one bulk action so the
    # Batch filter can present them as a single run.
    run_group: str | None = None


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_job(request: Request, body: JobCreate):
    require_permission(request, "run")
    ctx = get_core_ctx(request)
    job_id = await ctx.jobs_repo.create_job(
        ctx.db,
        prompt_version_id=body.prompt_version_id,
        clip_ids=body.clip_ids,
        run_group=body.run_group,
    )
    # The lifespan-owned JobRunner claims pending jobs when the live stack is
    # up; offline, the job stays pending and resumes on the next live boot.
    # Routes never execute jobs themselves (ADR 0125).
    return {"id": job_id, "queued": True}


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
                "run_group": job.run_group,
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
            model_config_repo=ctx.model_config_repo,
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
    # DB flip first (works offline): job + in-flight items → cancelled, so the
    # claimer never picks it up. Then, if a live worker is running this exact
    # job, interrupt it so cancel is prompt instead of waiting out the Gemini
    # call. Its CancelledError handler re-runs cancel_job (idempotent).
    await ctx.jobs_repo.cancel_job(ctx.db, job_id)
    live = request.app.state.live_ctx
    if live is not None and live.job_runner is not None:
        live.job_runner.cancel(job_id)
    return {"id": job_id, "status": "cancelled"}
