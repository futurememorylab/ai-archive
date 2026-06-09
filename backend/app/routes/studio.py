"""REST API for Prompt Studio — sets, set_clips, runs.

All under /api/studio. See docs/specs/2026-05-26-prompt-studio-design.md.
"""

import asyncio

import aiosqlite
from fastapi import (
    APIRouter,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import Response
from pydantic import BaseModel

from backend.app.deps import get_core_ctx
from backend.app.routes.pages.templates import templates
from backend.app.services.annotator import run_job
from backend.app.uploaded_ids import to_clip_id

router = APIRouter(prefix="/api/studio", tags=["studio"])


# ── request models ──────────────────────────────────────────────────────────


class SetCreate(BaseModel):
    name: str


class SetPatch(BaseModel):
    name: str


class AddClips(BaseModel):
    clip_ids: list[int]


class RunCreate(BaseModel):
    prompt_version_id: int
    clip_id: int
    model: str | None = None


# ── sets ─────────────────────────────────────────────────────────────────────


@router.get("/sets")
async def list_sets(request: Request, source: str = "archive"):
    ctx = get_core_ctx(request)
    return await ctx.studio_sets_repo.list_sets_with_counts(ctx.db, source=source)


@router.post("/sets", status_code=status.HTTP_201_CREATED)
async def create_set(
    request: Request,
    body: SetCreate,
    source: str = "archive",
    hx_request: str | None = Header(None, alias="HX-Request"),
):
    ctx = get_core_ctx(request)
    try:
        sid = await ctx.studio_sets_repo.create_set(ctx.db, name=body.name, source=source)
    except aiosqlite.IntegrityError as exc:
        raise HTTPException(409, f"set name {body.name!r} already exists") from exc

    if hx_request == "true":
        s = {"id": sid, "name": body.name, "clip_count": 0}
        return templates.TemplateResponse(
            request,
            "pages/_studio_set_card.html",
            {"f": s, "active_version": None, "focused_clip_id": None},
        )
    return {"id": sid}


@router.patch("/sets/{set_id}")
async def rename_set(request: Request, set_id: int, body: SetPatch):
    ctx = get_core_ctx(request)
    try:
        await ctx.studio_sets_repo.rename_set(ctx.db, set_id, name=body.name)
    except aiosqlite.IntegrityError as exc:
        raise HTTPException(409, f"set name {body.name!r} already exists") from exc
    return {"id": set_id, "name": body.name}


@router.delete("/sets/{set_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_set(request: Request, set_id: int):
    ctx = get_core_ctx(request)
    await ctx.studio_sets_repo.delete_set(ctx.db, set_id)
    return Response(status_code=204)


# ── set clips ─────────────────────────────────────────────────────────────────


@router.get("/sets/{set_id}/clips")
async def list_set_clips(request: Request, set_id: int):
    ctx = get_core_ctx(request)
    return await ctx.studio_sets_repo.list_clips(ctx.db, set_id)


@router.post("/sets/{set_id}/clips")
async def add_set_clips(
    request: Request,
    set_id: int,
    body: AddClips,
    hx_request: str | None = Header(None, alias="HX-Request"),
):
    ctx = get_core_ctx(request)
    added = await ctx.studio_sets_repo.add_clips(ctx.db, set_id, clip_ids=body.clip_ids)
    if hx_request == "true":
        clips = await ctx.studio_sets_repo.list_clips(ctx.db, set_id)
        return templates.TemplateResponse(
            request,
            "pages/_studio_set.html",
            {"clips": clips, "set_id": set_id},
        )
    return {"added": added}


@router.delete("/sets/{set_id}/clips/{clip_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_set_clip(request: Request, set_id: int, clip_id: int):
    ctx = get_core_ctx(request)
    await ctx.studio_sets_repo.remove_clip(ctx.db, set_id, clip_id=clip_id)
    return Response(status_code=204)


# ── uploads (Spec B) ──────────────────────────────────────────────────────────

_EXT_BY_MIME = {"video/mp4": ".mp4", "video/webm": ".webm"}


@router.post("/uploads", status_code=status.HTTP_201_CREATED)
async def upload_clip(
    request: Request,
    file: UploadFile = File(...),
    poster: UploadFile | None = File(None),
    set_id: int | None = Form(None),
    duration_secs: float | None = Form(None),
    width: int | None = Form(None),
    height: int | None = Form(None),
    hx_request: str | None = Header(None, alias="HX-Request"),
):
    ctx = get_core_ctx(request)
    s = ctx.settings

    mime = (file.content_type or "").split(";")[0].strip()
    allowed = {m.strip() for m in s.studio_upload_allowed_mimes.split(",") if m.strip()}
    ext = _EXT_BY_MIME.get(mime)
    if mime not in allowed or ext is None:
        raise HTTPException(
            415, f"Unsupported format {mime or 'unknown'!r}; allowed: {sorted(allowed)}"
        )

    data = await file.read()
    max_bytes = int(s.studio_upload_max_mb) * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(
            413, f"File too large ({len(data)} bytes); max {s.studio_upload_max_mb} MB"
        )

    if set_id is None:
        set_id = await ctx.studio_sets_repo.get_or_create_default_uploaded_set(ctx.db)

    pk = await ctx.uploaded_clips_repo.create(
        ctx.db,
        original_filename=file.filename or "upload",
        mime=mime,
        size_bytes=len(data),
        ext=ext,
        duration_secs=duration_secs,
        width=width,
        height=height,
    )
    clip_id = to_clip_id(pk)

    uploads_dir = s.data_dir / "cache" / "uploads"
    dest = uploads_dir / f"{clip_id}{ext}"

    def _write_video() -> None:
        uploads_dir.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)  # sync-io-ok: runs inside asyncio.to_thread (_write_video)

    await asyncio.to_thread(_write_video)

    await ctx.proxy_cache_repo.record(
        ctx.db,
        clip_id=clip_id,
        file_path=str(dest),
        size_bytes=len(data),
        etag=None,
        provider_id="uploaded",
        provider_clip_id=str(clip_id),
    )

    if poster is not None:
        poster_bytes = await poster.read()
        thumbs_dir = s.data_dir / "cache" / "thumbs"
        thumb_dest = thumbs_dir / f"{clip_id}.jpg"

        def _write_poster() -> None:
            thumbs_dir.mkdir(parents=True, exist_ok=True)
            thumb_dest.write_bytes(poster_bytes)  # sync-io-ok: runs inside asyncio.to_thread (_write_poster)

        await asyncio.to_thread(_write_poster)

    await ctx.studio_sets_repo.add_clips(ctx.db, set_id, clip_ids=[clip_id])

    if hx_request == "true":
        c = {
            "clip_id": clip_id,
            "name": file.filename or f"upload-{clip_id}",
            "duration_secs": duration_secs,
            "year": None,
            "fps": 25.0,
            "has_cur": False,
            "has_other": False,
            "uploaded": True,
        }
        return templates.TemplateResponse(
            request,
            "pages/_studio_set_clip_card.html",
            {"c": c, "set_id": set_id, "focused_clip_id": None},
            status_code=status.HTTP_201_CREATED,
        )
    return {"clip_id": clip_id, "set_id": set_id}


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
            uploaded_clips_repo=ctx.uploaded_clips_repo,
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
