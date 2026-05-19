"""WriteQueue: group accepted review items into ChangeOps and enqueue them.

The apply route used to build ChangeOps and PUT to CatDV inline. PR 4 splits
that into (a) enqueue the ops as durable `pending_operations` rows, (b) let
the SyncEngine drain them. This module owns step (a) and the item→op
grouping logic that previously lived in `routes/review.py`.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from backend.app.archive.change_set_json import change_op_to_json
from backend.app.archive.model import (
    AddMarkers,
    AppendNote,
    ChangeOp,
    ClipKey,
    Marker,
    ReplaceNote,
    SetField,
    Timecode,
)
from backend.app.models.annotation import ReviewItem
from backend.app.models.template import TargetMap
from backend.app.repositories.pending_operations import PendingOperationsRepo
from backend.app.repositories.review_items import ReviewItemsRepo


class WriteQueue:
    def __init__(
        self,
        *,
        pending_ops_repo: PendingOperationsRepo,
        review_items_repo: ReviewItemsRepo,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._pending = pending_ops_repo
        self._review_items = review_items_repo
        self._clock = clock or (lambda: datetime.now(UTC))

    async def enqueue_apply(
        self,
        conn: aiosqlite.Connection,
        *,
        clip_key: ClipKey,
        items: list[ReviewItem],
        target_map: TargetMap,
        expected_etag: str | None,
        annotation_id: int | None,
        fps: float,
    ) -> list[int]:
        """Group accepted items into ChangeOps and write one row per op.

        Items already marked applied (`applied_at IS NOT NULL`) are skipped,
        so a double-click does not produce duplicate ops. The pending_ops
        insert and the `mark_applied` update commit together.
        """
        provider_id, provider_clip_id = clip_key
        fresh_items = [it for it in items if it.applied_at is None and it.id is not None]
        if not fresh_items:
            return []

        ops_with_origin = _items_to_change_ops(fresh_items, target_map, fps=fps)
        if not ops_with_origin:
            return []

        rows = []
        for op, origin_ids in ops_with_origin:
            rows.append(
                {
                    "provider_id": provider_id,
                    "provider_clip_id": provider_clip_id,
                    "op_kind": type(op).__name__,
                    "op_json": change_op_to_json(op),
                    "origin_annotation_id": annotation_id,
                    "origin_review_item_ids": origin_ids,
                    "expected_etag": expected_etag,
                }
            )

        op_ids = await self._pending.insert_many(conn, rows=rows, commit=False)
        item_ids = [it.id for it in fresh_items if it.id is not None]
        await self._review_items.mark_applied(conn, item_ids, commit=False)
        await conn.commit()
        return op_ids


# --- ops grouping (moved out of routes/review.py) -------------------------


def _items_to_change_ops(
    items: list[ReviewItem],
    target_map: TargetMap,
    *,
    fps: float,
) -> list[tuple[ChangeOp, list[int]]]:
    """Group items into ChangeOps, paired with their originating review_item ids."""
    ops: list[tuple[ChangeOp, list[int]]] = []
    marker_payloads: list[Marker] = []
    marker_origin: list[int] = []
    for it in items:
        if it.id is None:
            continue
        value = it.edited_value if it.edited_value is not None else it.proposed_value
        if it.kind == "marker" and isinstance(value, dict):
            m = _marker_from_review_value(value, fps)
            if m is not None:
                marker_payloads.append(m)
                marker_origin.append(it.id)
        elif it.kind == "field" and it.target_identifier:
            ops.append(
                (
                    SetField(identifier=it.target_identifier, value=_unwrap(value)),
                    [it.id],
                )
            )
        elif it.kind == "note" and it.target_identifier:
            mode = _note_mode(target_map, it.target_identifier)
            text = str(_unwrap(value))
            if mode == "replace":
                ops.append((ReplaceNote(target=it.target_identifier, text=text), [it.id]))
            else:
                ops.append((AppendNote(target=it.target_identifier, text=text), [it.id]))
    if marker_payloads:
        ops.insert(0, (AddMarkers(markers=tuple(marker_payloads)), marker_origin))
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


def fps_from_snapshot(snapshot: dict[str, Any]) -> float:
    v = snapshot.get("fps") if isinstance(snapshot, dict) else None
    if isinstance(v, (int, float)) and v > 0:
        return float(v)
    return 25.0


def etag_from_snapshot(snapshot: dict[str, Any]) -> str | None:
    """CatDV pseudo-etag: the `modifyDate` field captured at snapshot time."""
    if not isinstance(snapshot, dict):
        return None
    v = snapshot.get("modifyDate")
    return str(v) if v is not None else None
