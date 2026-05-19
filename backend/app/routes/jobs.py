import asyncio

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from pydantic import BaseModel

from backend.app.services.annotator import run_job

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class JobCreate(BaseModel):
    template_id: int
    clip_ids: list[int]
    auto_start: bool = True


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_job(request: Request, body: JobCreate, background: BackgroundTasks):
    ctx = request.app.state.ctx
    job_id = await ctx.jobs_repo.create_job(
        ctx.db,
        template_id=body.template_id,
        clip_ids=body.clip_ids,
    )
    if body.auto_start and ctx.archive and ctx.ai_store and ctx.gemini and ctx.proxy_resolver:
        task = asyncio.create_task(_run_in_bg(ctx, job_id))
        ctx._running_jobs[job_id] = task
    return {"id": job_id}


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
            templates_repo=ctx.templates_repo,
        )
    finally:
        ctx._running_jobs.pop(job_id, None)


@router.get("")
async def list_jobs(request: Request, limit: int = 50):
    ctx = request.app.state.ctx
    return [j.model_dump() for j in await ctx.jobs_repo.list_jobs(ctx.db, limit=limit)]


@router.get("/{job_id}")
async def get_job(request: Request, job_id: int):
    ctx = request.app.state.ctx
    try:
        job = await ctx.jobs_repo.get_job(ctx.db, job_id)
    except LookupError:
        raise HTTPException(404, "job not found")
    items = await ctx.jobs_repo.list_items(ctx.db, job_id)
    return {**job.model_dump(), "items": [it.model_dump() for it in items]}


@router.post("/{job_id}/cancel")
async def cancel_job(request: Request, job_id: int):
    ctx = request.app.state.ctx
    await ctx.jobs_repo.update_status(ctx.db, job_id, "cancelled")
    return {"id": job_id, "status": "cancelled"}
