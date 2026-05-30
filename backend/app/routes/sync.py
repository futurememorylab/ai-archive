"""Sync drawer HTTP surface.

Lists the `pending_operations` rows behind the drawer, plus the three
actions that operate on them: run a drain, retry a single row, discard
a single row.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from backend.app.deps import get_core_ctx, get_live_ctx

router = APIRouter(prefix="/api/sync", tags=["sync"])


@router.get("/pending")
async def list_pending(request: Request) -> list[dict]:
    ctx = get_core_ctx(request)
    rows = await ctx.pending_ops_repo.list_with_clip_names(ctx.db)
    return rows


@router.post("/run")
async def run_drain(request: Request) -> dict:
    ctx = get_live_ctx(request)
    processed = await ctx.sync_engine.drain_once()
    return {"processed": processed}


@router.post("/pending/{op_id}/retry")
async def retry_op(request: Request, op_id: int) -> dict:
    ctx = get_core_ctx(request)
    n = await ctx.pending_ops_repo.reset_for_retry(ctx.db, op_id)
    if n == 0:
        raise HTTPException(404, "pending op not found")
    # Nudge the sync engine to drain immediately, if live.
    live = request.app.state.live_ctx
    if live is not None:
        live.sync_engine.notify()
    return {"id": op_id, "reset": True}


@router.post("/pending/{op_id}/discard")
async def discard_op(request: Request, op_id: int) -> dict:
    ctx = get_core_ctx(request)
    n = await ctx.pending_ops_repo.delete(ctx.db, op_id)
    if n == 0:
        raise HTTPException(404, "pending op not found")
    return {"id": op_id, "discarded": True}
