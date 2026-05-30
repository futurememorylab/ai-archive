"""Jobs routes — HTTP endpoints under /api/jobs for creating and
inspecting annotation jobs. Delegates execution to the annotator service."""

import asyncio

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from backend.app.deps import get_ctx
from backend.app.routes.events import _event_generator
from backend.app.services.annotator import JOBS_TOPIC, run_job

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class JobCreate(BaseModel):
    prompt_version_id: int
    clip_ids: list[int]
    auto_start: bool = True


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_job(request: Request, body: JobCreate, background: BackgroundTasks):
    ctx = get_ctx(request)
    job_id = await ctx.jobs_repo.create_job(
        ctx.db,
        prompt_version_id=body.prompt_version_id,
        clip_ids=body.clip_ids,
    )
    started = bool(
        body.auto_start and ctx.archive and ctx.ai_store and ctx.gemini and ctx.proxy_resolver
    )
    if started:
        task = asyncio.create_task(_run_in_bg(ctx, job_id))
        ctx._running_jobs[job_id] = task
    return {"id": job_id, "started": started}


async def _run_in_bg(ctx, job_id: int) -> None:
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
        )
    finally:
        ctx._running_jobs.pop(job_id, None)


@router.get("")
async def list_jobs(request: Request, limit: int = 50):
    ctx = get_ctx(request)
    return [j.model_dump() for j in await ctx.jobs_repo.list_jobs(ctx.db, limit=limit)]


@router.get("/active")
async def list_active_jobs(request: Request):
    """Running jobs with progress counts — powers the topbar indicator."""
    ctx = get_ctx(request)
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
            }
        )
    return out


@router.get("/events")
async def jobs_events(request: Request):
    """SSE stream of the global `jobs` topic — powers the topbar indicator."""
    ctx = get_ctx(request)

    async def stream():
        async for frame in _event_generator(ctx.event_bus, topic=JOBS_TOPIC):
            if await request.is_disconnected():
                return
            yield {"data": frame.removeprefix("data: ").rstrip("\n")}

    return EventSourceResponse(stream())


@router.get("/{job_id}")
async def get_job(request: Request, job_id: int):
    ctx = get_ctx(request)
    try:
        job = await ctx.jobs_repo.get_job(ctx.db, job_id)
    except LookupError:
        raise HTTPException(404, "job not found") from None
    items = await ctx.jobs_repo.list_items(ctx.db, job_id)
    return {**job.model_dump(), "items": [it.model_dump() for it in items]}


@router.post("/{job_id}/cancel")
async def cancel_job(request: Request, job_id: int):
    ctx = get_ctx(request)
    await ctx.jobs_repo.update_status(ctx.db, job_id, "cancelled")
    return {"id": job_id, "status": "cancelled"}
