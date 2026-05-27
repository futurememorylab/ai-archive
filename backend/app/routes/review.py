"""Review routes — HTTP endpoints under /api/review for listing review
items per clip, setting accept/reject decisions, and enqueuing the
upstream apply via the write queue."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.app.context import AppContext
from backend.app.deps import get_ctx
from backend.app.services.write_queue import etag_from_snapshot, fps_from_snapshot

router = APIRouter(prefix="/api/review", tags=["review"])

_VALID_KINDS = {"marker", "field", "note"}


class Decision(BaseModel):
    decision: str
    edited_value: Any = None


class ApplyBatch(BaseModel):
    clip_ids: list[int]
    kinds: list[str] | None = None



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


async def _resolve_and_enqueue_clip(
    ctx: AppContext, clip_id: int, *, kinds: set[str] | None = None
) -> int:
    """Resolve a clip's accepted items + apply context and enqueue them.

    When `kinds` is given, only accepted items of those kinds are enqueued
    (so a kind-filtered bulk apply does not flush previously-accepted items
    of other kinds). Returns the number of ops queued."""
    accepted = await ctx.review_items_repo.list_by_clip(ctx.db, clip_id, decision="accepted")
    if kinds is not None:
        accepted = [it for it in accepted if it.kind in kinds]
    if not accepted:
        return 0
    annotation = await ctx.annotations_repo.get(ctx.db, accepted[0].annotation_id)
    version = await ctx.prompts_repo.get_version(ctx.db, annotation.prompt_version_id)
    op_ids = await ctx.write_queue.enqueue_apply_for_clip(
        ctx.db,
        clip_id=clip_id,
        accepted=accepted,
        target_map=version.target_map,
        expected_etag=etag_from_snapshot(annotation.clip_snapshot),
        annotation_id=annotation.id,
        fps=fps_from_snapshot(annotation.clip_snapshot),
    )
    return len(op_ids)


@router.post("/apply-batch")
async def apply_batch(request: Request, body: ApplyBatch):
    """Accept all un-applied items of the given kinds on the given clips,
    then enqueue apply for each (the "yolo" bulk path).

    Unknown clip_ids or clips with no matching un-applied items are skipped
    silently (contribute 0); the loop is not atomic across clips (partial
    progress is recoverable via the durable pending-ops queue).
    """
    ctx = get_ctx(request)
    if ctx.write_queue is None:
        raise HTTPException(503, "write queue not initialized")
    kinds = set(body.kinds) if body.kinds else set(_VALID_KINDS)
    if not kinds <= _VALID_KINDS:
        raise HTTPException(400, "kinds must be a subset of marker|field|note")

    total_queued = 0
    clips_touched = 0
    for clip_id in body.clip_ids:
        pending = await ctx.review_items_repo.list_by_clip(ctx.db, clip_id, decision=None)
        to_accept = [it for it in pending if it.applied_at is None and it.kind in kinds]
        if not to_accept:
            continue
        for it in to_accept:
            await ctx.review_items_repo.set_decision(ctx.db, it.id, "accepted")
        queued = await _resolve_and_enqueue_clip(ctx, clip_id, kinds=kinds)
        if queued:
            clips_touched += 1
            total_queued += queued
    if total_queued and ctx.sync_engine is not None:
        ctx.sync_engine.notify()
    return {"clips": clips_touched, "queued": total_queued}


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
    queued = await _resolve_and_enqueue_clip(ctx, clip_id)
    if queued and ctx.sync_engine is not None:
        ctx.sync_engine.notify()
    return {"queued": queued, "applied": queued}
