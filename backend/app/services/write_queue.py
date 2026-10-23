"""WriteQueue: group accepted review items into ChangeOps and enqueue them.

The apply route used to build ChangeOps and PUT to CatDV inline. PR 4 splits
that into (a) enqueue the ops as durable `pending_operations` rows, (b) let
the SyncEngine drain them. This module owns step (a) and the item→op
grouping logic that previously lived in `routes/review.py`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from backend.app.archive.change_set_json import change_op_to_json
from backend.app.archive.model import (
    AI_OWNED_FIELDS,
    AppendNote,
    ChangeOp,
    ClipKey,
    Marker,
    ReplaceAIMarkers,
    ReplaceNote,
    SetField,
    Timecode,
)
from backend.app.models.annotation import ReviewItem
from backend.app.models.prompt import TargetMap
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

    async def enqueue_apply_for_clip(
        self,
        conn: aiosqlite.Connection,
        *,
        clip_id: int,
        accepted: list[ReviewItem],
        target_map: TargetMap,
        expected_etag: str | None,
        annotation_id: int | None,
        fps: float,
        clip_version_id: int | None = None,
        extra_ops: list[ChangeOp] | None = None,
    ) -> list[int]:
        """Thin wrapper over enqueue_apply keyed by a catdv clip id."""
        return await self.enqueue_apply(
            conn,
            clip_key=("catdv", str(clip_id)),
            items=accepted,
            target_map=target_map,
            expected_etag=expected_etag,
            annotation_id=annotation_id,
            fps=fps,
            clip_version_id=clip_version_id,
            extra_ops=extra_ops,
        )

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
        clip_version_id: int | None = None,
        extra_ops: list[ChangeOp] | None = None,
    ) -> list[int]:
        """Group accepted items into ChangeOps and write one row per op.

        Items already marked applied (`applied_at IS NOT NULL`) are skipped,
        so a double-click does not produce duplicate ops. The pending_ops
        insert and the `mark_applied` update commit together.

        `clip_version_id` is stamped onto every row as `origin_clip_version_id`
        so the SyncEngine can flip that version live once the ops land.
        `extra_ops` (e.g. provenance SetField writes) are appended after the
        item-derived ops with an empty origin review-item list.
        """
        fresh_items = [it for it in items if it.applied_at is None and it.id is not None]
        ops_with_origin = _items_to_change_ops(
            fresh_items,
            target_map,
            fps=fps,
            ai_marker_set=ai_marker_values(items, annotation_id),
        )
        for op in extra_ops or []:
            ops_with_origin.append((op, []))
        if not ops_with_origin:
            return []

        rows = [
            _op_row(
                clip_key,
                op,
                origin_ids,
                annotation_id=annotation_id,
                expected_etag=expected_etag,
                clip_version_id=clip_version_id,
            )
            for op, origin_ids in ops_with_origin
        ]

        # commit=False → the connection serializer (WriteSerializedConnection)
        # holds the write lock across this whole window until conn.commit(), so
        # no other writer can split it. See ADR 0126.
        op_ids = await self._pending.insert_many(conn, rows=rows, commit=False)
        # Mark applied ONLY the items that produced an owned op. A suggestion
        # (a field/note item outside AI_OWNED_FIELDS) is never written to the
        # archive (#123), so it stays un-applied — it keeps showing as a
        # copy-me suggestion and is excluded from the pending-review count.
        handled_ids = {i for _op, origin in ops_with_origin for i in origin}
        item_ids = [it.id for it in fresh_items if it.id is not None and it.id in handled_ids]
        if item_ids:
            await self._review_items.mark_applied(conn, item_ids, commit=False)
        await conn.commit()
        return op_ids

    async def enqueue_ops(
        self,
        conn: aiosqlite.Connection,
        *,
        clip_key: ClipKey,
        ops: list[ChangeOp],
        clip_version_id: int | None,
        commit: bool = True,
    ) -> list[int]:
        """Enqueue ChangeOps that come from no review item (e.g. removing the
        AI annotations). No expected etag: the payload builder works from the
        live clip and touches only what the app owns, so a human edit in the
        archive meanwhile is not a conflict."""
        rows = [
            _op_row(
                clip_key,
                op,
                [],
                annotation_id=None,
                expected_etag=None,
                clip_version_id=clip_version_id,
            )
            for op in ops
        ]
        op_ids = await self._pending.insert_many(conn, rows=rows, commit=False)
        if commit:
            await conn.commit()
        return op_ids


def _op_row(
    clip_key: ClipKey,
    op: ChangeOp,
    origin_ids: list[int],
    *,
    annotation_id: int | None,
    expected_etag: str | None,
    clip_version_id: int | None,
) -> dict[str, Any]:
    """One pending_operations row for `op` on `clip_key`."""
    provider_id, provider_clip_id = clip_key
    return {
        "provider_id": provider_id,
        "provider_clip_id": provider_clip_id,
        "op_kind": type(op).__name__,
        "op_json": change_op_to_json(op),
        "origin_annotation_id": annotation_id,
        "origin_review_item_ids": origin_ids,
        "expected_etag": expected_etag,
        "origin_clip_version_id": clip_version_id,
    }


# --- ops grouping (moved out of routes/review.py) -------------------------


def ai_marker_values(items: list[ReviewItem], annotation_id: int | None) -> list[dict[str, Any]]:
    """The clip's AI marker set for a publish: every accepted marker item of
    `annotation_id` (applied or not), as review values, deduplicated.

    One rule for both the CatDV write (ReplaceAIMarkers) and the version
    snapshot, so they can't diverge. Dedup by value covers Restore, which
    re-inserts a version's markers under the same annotation as fresh items.
    ponytail: restoring a sibling partial-apply version of the SAME annotation
    still unions its markers; anchor restored items to their own generation if
    that ever matters.
    """
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for it in items:
        if it.kind != "marker" or it.decision != "accepted" or it.annotation_id != annotation_id:
            continue
        value = it.edited_value if it.edited_value is not None else it.proposed_value
        if not isinstance(value, dict):
            continue
        key = json.dumps(value, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            out.append(value)
    return out


def _items_to_change_ops(
    items: list[ReviewItem],
    target_map: TargetMap,
    *,
    fps: float,
    ai_marker_set: list[dict[str, Any]] | None = None,
) -> list[tuple[ChangeOp, list[int]]]:
    """Group items into ChangeOps, paired with their originating review_item ids.

    The app emits only what it owns (#123): one ReplaceAIMarkers op carrying
    every accepted marker item (the replace set), and writes to AI_OWNED_FIELDS.
    A field/note item whose target is not owned is a *suggestion* — never
    enqueued — so a shared field like decade or year is offered for the human to
    copy, never written. A draft with no marker items emits no marker op, so
    publishing it never wipes the clip's AI markers.
    """
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
            if it.target_identifier not in AI_OWNED_FIELDS:
                continue  # suggestion — never written to the archive
            ops.append(
                (
                    SetField(identifier=it.target_identifier, value=_unwrap(value)),
                    [it.id],
                )
            )
        elif it.kind == "note" and it.target_identifier:
            if it.target_identifier not in AI_OWNED_FIELDS:
                continue  # suggestion — never written to the archive
            mode = _note_mode(target_map, it.target_identifier)
            text = str(_unwrap(value))
            if mode == "replace":
                ops.append((ReplaceNote(target=it.target_identifier, text=text), [it.id]))
            else:
                ops.append((AppendNote(target=it.target_identifier, text=text), [it.id]))
    if marker_payloads:
        # The replace set is the whole AI set (every accepted marker of the
        # annotation), not just this batch — a second partial apply must not
        # drop the markers the first apply wrote.
        if ai_marker_set:
            desired = [m for v in ai_marker_set if (m := _marker_from_review_value(v, fps))]
        else:
            desired = marker_payloads
        ops.insert(0, (ReplaceAIMarkers(desired=tuple(desired)), marker_origin))
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
    if isinstance(value, dict) and "value" in value:
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
