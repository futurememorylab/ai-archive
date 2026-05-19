from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.app.archive.errors import ProviderError
from backend.app.archive.model import (
    AddMarkers,
    AppendNote,
    ChangeOp,
    ChangeSet,
    Marker,
    ReplaceNote,
    SetField,
    Timecode,
)
from backend.app.models.annotation import ReviewItem
from backend.app.models.template import TargetMap

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
    if ctx.archive is None:
        raise HTTPException(503, "archive provider not initialized")

    accepted = await ctx.review_items_repo.list_by_clip(ctx.db, clip_id, decision="accepted")
    if not accepted:
        return {"applied": 0}

    annotation = await ctx.annotations_repo.get(ctx.db, accepted[0].annotation_id)
    template = await ctx.templates_repo.get(ctx.db, annotation.template_id)

    ops = _items_to_change_ops(
        accepted, template.target_map, fps=_fps_from_snapshot(annotation.clip_snapshot)
    )
    if not ops:
        return {"applied": 0}

    change_set = ChangeSet(clip_key=("catdv", str(clip_id)), ops=tuple(ops))

    try:
        result = await ctx.archive.apply_changes(change_set)
    except ProviderError as exc:
        await ctx.write_log_repo.record(
            ctx.db,
            catdv_clip_id=clip_id,
            annotation_id=annotation.id,
            payload={"ops": [type(o).__name__ for o in ops]},
            response={"error": str(exc)},
            status="error",
        )
        raise HTTPException(502, f"archive apply failed: {exc}")

    await ctx.write_log_repo.record(
        ctx.db,
        catdv_clip_id=clip_id,
        annotation_id=annotation.id,
        payload={"ops": [type(o).__name__ for o in ops]},
        response=result.upstream_response,
        status="ok",
    )
    await ctx.review_items_repo.mark_applied(
        ctx.db,
        [it.id for it in accepted if it.id is not None],
    )
    return {"applied": len(accepted)}


def _fps_from_snapshot(snapshot: dict[str, Any]) -> float:
    v = snapshot.get("fps") if isinstance(snapshot, dict) else None
    if isinstance(v, (int, float)) and v > 0:
        return float(v)
    return 25.0


def _items_to_change_ops(
    items: list[ReviewItem],
    target_map: TargetMap,
    *,
    fps: float,
) -> list[ChangeOp]:
    ops: list[ChangeOp] = []
    new_markers: list[Marker] = []
    for it in items:
        value = it.edited_value if it.edited_value is not None else it.proposed_value
        if it.kind == "marker" and isinstance(value, dict):
            marker = _marker_from_review_value(value, fps)
            if marker is not None:
                new_markers.append(marker)
        elif it.kind == "field" and it.target_identifier:
            ops.append(SetField(identifier=it.target_identifier, value=_unwrap(value)))
        elif it.kind == "note" and it.target_identifier:
            mode = _note_mode(target_map, it.target_identifier)
            text = str(_unwrap(value))
            if mode == "replace":
                ops.append(ReplaceNote(target=it.target_identifier, text=text))
            else:
                ops.append(AppendNote(target=it.target_identifier, text=text))
    if new_markers:
        ops.insert(0, AddMarkers(markers=tuple(new_markers)))
    return ops


def _marker_from_review_value(value: dict[str, Any], fps: float) -> Marker | None:
    name = value.get("name")
    in_obj = value.get("in")
    if not isinstance(name, str) or not isinstance(in_obj, dict):
        return None
    in_secs = in_obj.get("secs")
    if not isinstance(in_secs, (int, float)):
        return None
    out_obj = value.get("out") if isinstance(value.get("out"), dict) else None
    out_tc = None
    if out_obj is not None and isinstance(out_obj.get("secs"), (int, float)):
        out_tc = Timecode(secs=float(out_obj["secs"]), fps=fps)
    return Marker(
        name=name,
        in_=Timecode(secs=float(in_secs), fps=fps),
        out=out_tc,
        description=value.get("description"),
        category=value.get("category"),
        color=value.get("color"),
    )


def _unwrap(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value and "evidence_secs" in value:
        return value["value"]
    return value


def _note_mode(target_map: TargetMap, identifier: str) -> str:
    for entry in target_map.fields.values():
        if entry.kind == "note" and entry.target == identifier:
            return entry.mode
    return "append"
