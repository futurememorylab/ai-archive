"""Studio routes — pages + JSON API for the sandbox prompt-iteration UI.

Studio runs alongside the production annotate/review/write pipeline but
shares none of its tables; all reads and writes go through
`TestbenchesRepo`, `TestbenchItemsRepo`, `StudioRunsRepo`. Routes are
served regardless of CatDV's connection state.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated

import aiosqlite
from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse

from backend.app.context import AppContext
from backend.app.deps import get_ctx
from backend.app.services.studio_runs import StudioRunsService
from backend.app.services.studio_uploads import UploadError, save_upload

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/studio", tags=["studio"])


@router.post("/testbenches")
async def create_testbench(
    body: Annotated[dict, Body()],
    ctx: AppContext = Depends(get_ctx),
):
    try:
        new_id = await ctx.testbenches_repo.create(
            ctx.db, name=body["name"], description=body.get("description"),
        )
    except aiosqlite.IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="name already exists")
    tb = await ctx.testbenches_repo.get(ctx.db, new_id)
    return tb.model_dump()


@router.post("/testbenches/{tb_id}:rename")
async def rename_testbench(
    tb_id: int, body: dict = Body(...), ctx: AppContext = Depends(get_ctx),
):
    await ctx.testbenches_repo.rename(ctx.db, tb_id, body["name"])
    return {"ok": True}


@router.post("/testbenches/{tb_id}:archive")
async def archive_testbench(tb_id: int, ctx: AppContext = Depends(get_ctx)):
    await ctx.testbenches_repo.archive(ctx.db, tb_id)
    return {"ok": True}


@router.post("/testbenches/{tb_id}/folders")
async def create_folder(
    tb_id: int, body: dict = Body(...), ctx: AppContext = Depends(get_ctx),
):
    folder_id = await ctx.testbenches_repo.create_folder(
        ctx.db, testbench_id=tb_id, parent_id=body.get("parent_id"), name=body["name"],
    )
    return {"id": folder_id, "parent_id": body.get("parent_id"), "name": body["name"]}


@router.post("/folders/{folder_id}:rename")
async def rename_folder(
    folder_id: int, body: dict = Body(...), ctx: AppContext = Depends(get_ctx),
):
    await ctx.testbenches_repo.rename_folder(ctx.db, folder_id, body["name"])
    return {"ok": True}


@router.delete("/folders/{folder_id}")
async def delete_folder(folder_id: int, ctx: AppContext = Depends(get_ctx)):
    try:
        await ctx.testbenches_repo.delete_folder(ctx.db, folder_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc))
    return {"ok": True}


@router.post("/folders/{folder_id}/items:add_catdv")
async def add_catdv_item(
    folder_id: int, body: dict = Body(...), ctx: AppContext = Depends(get_ctx),
):
    item_id = await ctx.testbench_items_repo.add_catdv(
        ctx.db,
        folder_id=folder_id,
        provider_clip_id=body["provider_clip_id"],
        name=body["name"],
    )
    items = await ctx.testbench_items_repo.list_for_folder(ctx.db, folder_id)
    return next(it.model_dump() for it in items if it.id == item_id)


@router.post("/folders/{folder_id}/items:add_upload")
async def add_upload_item(
    folder_id: int,
    file: UploadFile = File(...),
    ctx: AppContext = Depends(get_ctx),
):
    try:
        rel = await save_upload(
            file,
            uploads_dir=ctx.settings.studio_uploads_dir,
            max_mb=ctx.settings.studio_max_upload_mb,
        )
    except UploadError as exc:
        msg = str(exc)
        if "unsupported content type" in msg:
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=msg)
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=msg)
    item_id = await ctx.testbench_items_repo.add_upload(
        ctx.db,
        folder_id=folder_id,
        upload_path=rel,
        original_name=file.filename or rel,
    )
    items = await ctx.testbench_items_repo.list_for_folder(ctx.db, folder_id)
    return next(it.model_dump() for it in items if it.id == item_id)


@router.put("/items/{item_id}/gold")
async def set_gold(
    item_id: int, body: dict = Body(...), ctx: AppContext = Depends(get_ctx),
):
    description = (body.get("description") or "").strip()
    if description == "" and len(body) == 1:
        await ctx.testbench_items_repo.set_gold(ctx.db, item_id, None)
    else:
        await ctx.testbench_items_repo.set_gold(ctx.db, item_id, body)
    return {"ok": True}


@router.delete("/items/{item_id}")
async def remove_item(item_id: int, ctx: AppContext = Depends(get_ctx)):
    await ctx.testbench_items_repo.remove(ctx.db, item_id)
    return {"ok": True}


def _build_studio_service(ctx: AppContext) -> StudioRunsService:
    """Construct (or reuse) the Studio runs service from current ctx state.

    Cached on ctx so multiple SSE/start calls share one service. The real
    AppContext.build will wire this once in Phase 9; this lazy path keeps
    routes working until then.
    """
    if ctx.studio_runs_service is not None:
        return ctx.studio_runs_service  # type: ignore[return-value]

    from backend.app.services.proxy_resolver import LocalCacheOnlyResolver

    cache_only = LocalCacheOnlyResolver(
        repo=ctx.proxy_cache_repo,
        db_provider=lambda c=ctx: c.db,
        cache_dir=ctx.settings.data_dir / "cache" / "proxies",
    )

    def mode_getter() -> str:
        cm = ctx.connection_monitor
        if cm is None:
            return "offline"
        from backend.app.services.connection_monitor import ConnectionState
        return "online" if cm.current_state() == ConnectionState.online else "offline"

    svc = StudioRunsService(
        runs_repo=ctx.studio_runs_repo,
        items_repo=ctx.testbench_items_repo,
        prompts_repo=ctx.prompts_repo,
        archive=ctx.archive,
        proxy_resolver=ctx.proxy_resolver,
        cache_only_resolver=cache_only,
        clip_cache_repo=ctx.clip_cache_repo,
        ai_store=ctx.ai_store,
        gemini=ctx.gemini,
        event_bus=ctx.event_bus,
        uploads_root=ctx.settings.studio_uploads_dir,
        mode_getter=mode_getter,
    )
    ctx.studio_runs_service = svc
    return svc


@router.post("/runs")
async def start_run(body: dict = Body(...), ctx: AppContext = Depends(get_ctx)):
    svc = _build_studio_service(ctx)
    run_id = await svc.create_run(
        ctx.db,
        testbench_id=body["testbench_id"],
        prompt_version_id=body["prompt_version_id"],
    )
    asyncio.create_task(svc.run(ctx.db, run_id))
    return {"id": run_id}


@router.post("/runs/{run_id}:cancel")
async def cancel_run(run_id: int, ctx: AppContext = Depends(get_ctx)):
    svc = _build_studio_service(ctx)
    await svc.cancel(ctx.db, run_id)
    return {"ok": True}


@router.get("/runs/{run_id}/events")
async def run_events(run_id: int, ctx: AppContext = Depends(get_ctx)):
    """SSE stream of per-item status events. Reuses the existing EventBus."""
    topic = f"studio_run:{run_id}"
    q = ctx.event_bus.subscribe(topic)

    async def stream():
        try:
            while True:
                msg = await q.get()
                yield f"data: {json.dumps(msg)}\n\n"
        finally:
            ctx.event_bus.unsubscribe(topic, q)

    return StreamingResponse(stream(), media_type="text/event-stream")
