"""Review routes — HTTP endpoints under /api/review for listing review
items per clip, setting accept/reject decisions, and enqueuing the
upstream apply via the write queue."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.app.deps import get_ctx
from backend.app.services.write_queue import etag_from_snapshot, fps_from_snapshot

router = APIRouter(prefix="/api/review", tags=["review"])


class Decision(BaseModel):
    decision: str
    edited_value: Any = None


@router.get("/clips/{clip_id}/items")
async def list_items_for_clip(request: Request, clip_id: int):
    ctx = get_ctx(request)
    items = await ctx.review_items_repo.list_by_clip(ctx.db, clip_id)
    return [it.model_dump() for it in items]


@router.post("/items/{item_id}/decision")
async def set_decision(request: Request, item_id: int, body: Decision):
    ctx = get_ctx(request)
    if body.decision not in ("accepted", "rejected", "pending"):
        raise HTTPException(400, "decision must be accepted|rejected|pending")
    await ctx.review_items_repo.set_decision(
        ctx.db,
        item_id,
        body.decision,
        edited_value=body.edited_value,
    )
    return {"id": item_id, "decision": body.decision}


@router.post("/clips/{clip_id}/apply")
async def apply_clip(request: Request, clip_id: int):
    """Enqueue accepted review items for upstream apply.

    The route used to PUT to CatDV synchronously. Now it writes one
    `pending_operations` row per ChangeOp (atomic with marking the
    review_items as `applied`) and notifies the SyncEngine to drain.
    When the engine is online the drain runs immediately, so the
    user-observable behaviour is unchanged ("applied: N"); when offline
    the ops sit in the queue until reconnection.
    """
    ctx = get_ctx(request)
    if ctx.write_queue is None:
        raise HTTPException(503, "write queue not initialized")

    accepted = await ctx.review_items_repo.list_by_clip(ctx.db, clip_id, decision="accepted")
    if not accepted:
        return {"queued": 0, "applied": 0}

    annotation = await ctx.annotations_repo.get(ctx.db, accepted[0].annotation_id)
    version = await ctx.prompts_repo.get_version(ctx.db, annotation.prompt_version_id)

    op_ids = await ctx.write_queue.enqueue_apply(
        ctx.db,
        clip_key=("catdv", str(clip_id)),
        items=accepted,
        target_map=version.target_map,
        expected_etag=etag_from_snapshot(annotation.clip_snapshot),
        annotation_id=annotation.id,
        fps=fps_from_snapshot(annotation.clip_snapshot),
    )
    if ctx.sync_engine is not None:
        ctx.sync_engine.notify()
    return {"queued": len(op_ids), "applied": len(op_ids)}
