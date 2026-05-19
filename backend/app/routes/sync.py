"""Sync drawer HTTP surface.

Lists the `pending_operations` rows behind the drawer, plus the three
actions that operate on them: run a drain, retry a single row, discard
a single row.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/sync", tags=["sync"])


@router.get("/pending")
async def list_pending(request: Request) -> list[dict]:
    ctx = request.app.state.ctx
    rows = await ctx.pending_ops_repo.list_with_clip_names(ctx.db)
    return rows


@router.post("/run")
async def run_drain(request: Request) -> dict:
    ctx = request.app.state.ctx
    if getattr(ctx, "sync_engine", None) is None:
        raise HTTPException(503, "sync engine not initialized")
    processed = await ctx.sync_engine.drain_once()
    return {"processed": processed}


@router.post("/pending/{op_id}/retry")
async def retry_op(request: Request, op_id: int) -> dict:
    ctx = request.app.state.ctx
    n = await ctx.pending_ops_repo.reset_for_retry(ctx.db, op_id)
    if n == 0:
        raise HTTPException(404, "pending op not found")
    if getattr(ctx, "sync_engine", None) is not None:
        ctx.sync_engine.notify()
    return {"id": op_id, "reset": True}


@router.post("/pending/{op_id}/discard")
async def discard_op(request: Request, op_id: int) -> dict:
    ctx = request.app.state.ctx
    n = await ctx.pending_ops_repo.delete(ctx.db, op_id)
    if n == 0:
        raise HTTPException(404, "pending op not found")
    return {"id": op_id, "discarded": True}
