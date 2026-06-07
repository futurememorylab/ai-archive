"""REST API for Prompt Studio — folders, folder_clips, runs.

All under /api/studio. See docs/specs/2026-05-26-prompt-studio-design.md.
"""

import asyncio

import aiosqlite
from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel

from backend.app.deps import get_core_ctx
from backend.app.routes.pages.templates import templates
from backend.app.services.annotator import run_job

router = APIRouter(prefix="/api/studio", tags=["studio"])


# ── request models ──────────────────────────────────────────────────────────


class FolderCreate(BaseModel):
    name: str


class FolderPatch(BaseModel):
    name: str


class AddClips(BaseModel):
    clip_ids: list[int]


class RunCreate(BaseModel):
    prompt_version_id: int
    clip_id: int
    model: str | None = None


# ── folders ─────────────────────────────────────────────────────────────────


@router.get("/folders")
async def list_folders(request: Request):
    ctx = get_core_ctx(request)
    return await ctx.studio_folders_repo.list_folders_with_counts(ctx.db)


@router.post("/folders", status_code=status.HTTP_201_CREATED)
async def create_folder(
    request: Request,
    body: FolderCreate,
    hx_request: str | None = Header(None, alias="HX-Request"),
):
    ctx = get_core_ctx(request)
    try:
        fid = await ctx.studio_folders_repo.create_folder(ctx.db, name=body.name)
    except aiosqlite.IntegrityError as exc:
        raise HTTPException(409, f"folder name {body.name!r} already exists") from exc

    if hx_request == "true":
        f = {"id": fid, "name": body.name, "clip_count": 0}
        return templates.TemplateResponse(
            request,
            "pages/_studio_folder_card.html",
            {"f": f, "active_version": None, "focused_clip_id": None},
        )
    return {"id": fid}


@router.patch("/folders/{folder_id}")
async def rename_folder(request: Request, folder_id: int, body: FolderPatch):
    ctx = get_core_ctx(request)
    try:
        await ctx.studio_folders_repo.rename_folder(ctx.db, folder_id, name=body.name)
    except aiosqlite.IntegrityError as exc:
        raise HTTPException(409, f"folder name {body.name!r} already exists") from exc
    return {"id": folder_id, "name": body.name}


@router.delete("/folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(request: Request, folder_id: int):
    ctx = get_core_ctx(request)
    await ctx.studio_folders_repo.delete_folder(ctx.db, folder_id)
    return Response(status_code=204)


# ── folder clips ────────────────────────────────────────────────────────────


@router.get("/folders/{folder_id}/clips")
async def list_folder_clips(request: Request, folder_id: int):
    ctx = get_core_ctx(request)
    return await ctx.studio_folders_repo.list_clips(ctx.db, folder_id)


@router.post("/folders/{folder_id}/clips")
async def add_folder_clips(
    request: Request,
    folder_id: int,
    body: AddClips,
    hx_request: str | None = Header(None, alias="HX-Request"),
):
    ctx = get_core_ctx(request)
    added = await ctx.studio_folders_repo.add_clips(
        ctx.db, folder_id, clip_ids=body.clip_ids
    )
    if hx_request == "true":
        clips = await ctx.studio_folders_repo.list_clips(ctx.db, folder_id)
        return templates.TemplateResponse(
            request,
            "pages/_studio_folder.html",
            {"clips": clips, "folder_id": folder_id},
        )
    return {"added": added}


@router.delete(
    "/folders/{folder_id}/clips/{clip_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_folder_clip(request: Request, folder_id: int, clip_id: int):
    ctx = get_core_ctx(request)
    await ctx.studio_folders_repo.remove_clip(ctx.db, folder_id, clip_id=clip_id)
    return Response(status_code=204)


# ── runs ────────────────────────────────────────────────────────────────────


@router.post("/runs", status_code=status.HTTP_201_CREATED)
async def create_run(request: Request, body: RunCreate):
    ctx = get_core_ctx(request)
    try:
        version = await ctx.prompts_repo.get_version(ctx.db, body.prompt_version_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    model = body.model or version.model

    run_id = await ctx.studio_runs_repo.create_pending(
        ctx.db,
        prompt_version_id=body.prompt_version_id,
        clip_id=body.clip_id,
        model=model,
    )
    job_id = await ctx.jobs_repo.create_job(
        ctx.db,
        prompt_version_id=body.prompt_version_id,
        clip_ids=[body.clip_id],
        kind="studio",
    )
    await ctx.studio_runs_repo.attach_job(ctx.db, run_id, job_id=job_id)

    # Auto-run only when the full live stack is wired (and the resolver is
    # not fs-only). Offline → run row is created but left for a later run.
    live = request.app.state.live_ctx
    if live is not None and live.proxy_resolver is not None:
        task = asyncio.create_task(_run_in_bg(live, job_id))
        ctx._running_jobs[job_id] = task

    return {"run_id": run_id, "job_id": job_id}


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
            run_telemetry_repo=ctx.run_telemetry_repo,
            telemetry_ctx=ctx.telemetry_ctx,
        )
    finally:
        ctx._running_jobs.pop(job_id, None)


@router.get("/runs/{run_id}")
async def get_run(request: Request, run_id: int):
    ctx = get_core_ctx(request)
    try:
        run = await ctx.studio_runs_repo.get(ctx.db, run_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return run.model_dump()


@router.get("/runs")
async def latest_run(
    request: Request,
    prompt_version_id: int,
    clip_id: int,
    latest: int = 1,
):
    """Latest run for (version, clip). `latest=1` is the only supported mode in v1."""
    if latest != 1:
        raise HTTPException(400, "only latest=1 is supported in v1")
    ctx = get_core_ctx(request)
    run = await ctx.studio_runs_repo.latest_for_pair(
        ctx.db, prompt_version_id=prompt_version_id, clip_id=clip_id
    )
    return run.model_dump() if run else None
