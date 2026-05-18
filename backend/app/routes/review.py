from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.app.services.payload_builder import build_put_payload

router = APIRouter(prefix="/api/review", tags=["review"])


class Decision(BaseModel):
    decision: str
    edited_value: Any = None


@router.get("/clips/{clip_id}/items")
async def list_items_for_clip(request: Request, clip_id: int):
    ctx = request.app.state.ctx
    items = await ctx.review_items_repo.list_by_clip(ctx.db, clip_id)
    return [it.model_dump() for it in items]


@router.post("/items/{item_id}/decision")
async def set_decision(request: Request, item_id: int, body: Decision):
    ctx = request.app.state.ctx
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
    ctx = request.app.state.ctx
    if ctx.catdv is None:
        raise HTTPException(503, "CatDV client not initialized")

    accepted = await ctx.review_items_repo.list_by_clip(ctx.db, clip_id, decision="accepted")
    if not accepted:
        return {"applied": 0, "payload": {}}

    try:
        current = await ctx.catdv.get_clip(clip_id)
    except Exception as exc:
        raise HTTPException(502, f"CatDV get_clip failed: {exc}")

    annotation = await ctx.annotations_repo.get(ctx.db, accepted[0].annotation_id)
    template = await ctx.templates_repo.get(ctx.db, annotation.template_id)

    payload = build_put_payload(
        current=current,
        accepted_items=accepted,
        target_map=template.target_map,
    )

    if not payload:
        return {"applied": 0, "payload": {}}

    try:
        response = await ctx.catdv.put_clip(clip_id, payload)
    except Exception as exc:
        await ctx.write_log_repo.record(
            ctx.db,
            catdv_clip_id=clip_id,
            annotation_id=annotation.id,
            payload=payload,
            response={"error": str(exc)},
            status="error",
        )
        raise HTTPException(502, f"CatDV put_clip failed: {exc}")

    await ctx.write_log_repo.record(
        ctx.db,
        catdv_clip_id=clip_id,
        annotation_id=annotation.id,
        payload=payload,
        response=response,
        status="ok",
    )
    await ctx.review_items_repo.mark_applied(
        ctx.db,
        [it.id for it in accepted if it.id is not None],
    )
    return {"applied": len(accepted), "payload": payload}
