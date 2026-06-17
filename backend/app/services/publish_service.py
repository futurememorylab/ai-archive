# backend/app/services/publish_service.py
"""PublishService — turns a clip's accepted working draft into an immutable
clip_versions row and drives it through the existing write queue.

Flow (see docs/specs/2026-06-17-clip-version-history-design.md §Publish):
  1. resolve accepted, annotation-bound review_items
  2. materialize the FULL committed snapshot (current live/CatDV state + accepted)
  3. insert clip_versions row (publishing) + diff vs the live parent
  4. enqueue ops via WriteQueue, stamped with the version id
  5. mark items applied (done inside the write queue)

The SyncEngine flips the row live (Task 7) when CatDV confirms.

NOTE: an earlier design wrote a `pragafilm.anno_version` provenance field to
CatDV on every publish (ADR 0099). That field is not defined in CatDV's schema,
so the PUT 500'd and blocked every annotation write — it was dropped. History
lives wholly in our app; CatDV gets only the real annotation changes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import aiosqlite

from backend.app.archive.model import ChangeOp, Marker, ReconcileMarkers, ReplaceNote, SetField
from backend.app.models.annotation import ClipVersion
from backend.app.models.prompt import TargetMap
from backend.app.services.write_queue import (
    _marker_from_review_value,
    etag_from_snapshot,
    fps_from_snapshot,
)

SnapshotLoader = Callable[[aiosqlite.Connection, int], Awaitable[dict[str, Any]]]
DEFAULT_FPS = 25.0


class PublishService:
    def __init__(
        self,
        *,
        annotations_repo,
        review_items_repo,
        clip_versions_repo,
        write_queue,
        prompts_repo,
        live_snapshot_loader: SnapshotLoader,
    ) -> None:
        self._annotations = annotations_repo
        self._review_items = review_items_repo
        self._versions = clip_versions_repo
        self._wq = write_queue
        self._prompts = prompts_repo
        self._load_live = live_snapshot_loader

    async def publish(
        self,
        conn: aiosqlite.Connection,
        *,
        clip_id: int,
        author: str | None,
        origin: str = "publish",
    ) -> int | None:
        accepted = await self._review_items.list_by_clip(conn, clip_id, decision="accepted")
        accepted = [it for it in accepted if it.annotation_id is not None and it.applied_at is None]
        if not accepted:
            return None

        annotation = await self._annotations.get(conn, accepted[0].annotation_id)
        version = await self._prompts.get_version(conn, annotation.prompt_version_id)
        fps = fps_from_snapshot(annotation.clip_snapshot)

        parent = await self._versions.live_for_clip(conn, clip_id)
        base = dict(parent.snapshot) if parent is not None else await self._load_live(conn, clip_id)
        snapshot = _materialize(base, accepted, fps=fps)

        num = await self._versions.next_version_num(conn, clip_id)
        version_id = await self._versions.insert(
            conn,
            ClipVersion(
                catdv_clip_id=clip_id,
                version_num=num,
                parent_version_id=parent.id if parent else None,
                snapshot=snapshot,
                diff=_diff(base, snapshot),
                origin=origin,
                model=annotation.model,
                prompt_version_id=annotation.prompt_version_id,
                annotation_id=annotation.id,
                author=author,
                publish_state="publishing",
                expected_etag=etag_from_snapshot(annotation.clip_snapshot),
            ),
        )

        await self._wq.enqueue_apply_for_clip(
            conn,
            clip_id=clip_id,
            accepted=accepted,
            target_map=version.target_map,
            expected_etag=etag_from_snapshot(annotation.clip_snapshot),
            annotation_id=annotation.id,
            fps=fps,
            clip_version_id=version_id,
        )
        return version_id

    async def reactivate(
        self, conn: aiosqlite.Connection, *, clip_id: int, version_num: int
    ) -> int:
        """Switch a clip back to an existing published version WITHOUT creating
        a new row.

        Re-PUTs the chosen version's snapshot to CatDV and (on success) marks it
        live again, superseding the current live. This is the 'switch versions'
        action — distinct from publish-forward, which forked a new identical
        version on every restore and proliferated history. Re-bases on the live
        clip (expected_etag=None): 'make this live' is an explicit override of
        whatever is currently on the clip. See the publishing audit, A3/A4.
        """
        versions = await self._versions.list_by_clip(conn, clip_id)
        target = next((v for v in versions if v.version_num == version_num), None)
        if target is None:
            raise LookupError(f"clip {clip_id} has no version {version_num}")
        if target.publish_state == "live":
            return target.id  # already live — no-op

        ops = _switch_ops(target, versions)
        await self._versions.mark_publishing(conn, target.id)
        if not ops:
            # Nothing to assert and nothing to drop — it's live in effect.
            await self._versions.mark_live(conn, target.id)
            return target.id
        await self._wq.enqueue_apply(
            conn,
            clip_key=("catdv", str(clip_id)),
            items=[],
            target_map=TargetMap({}),
            expected_etag=None,
            annotation_id=target.annotation_id,
            fps=DEFAULT_FPS,
            clip_version_id=target.id,
            extra_ops=ops,
        )
        return target.id


def _switch_ops(target: ClipVersion, versions: list[ClipVersion]) -> list[ChangeOp]:
    """Ops that switch a clip to `target`'s snapshot (the 'Make live' path).

    Markers are reconciled, not merely added: we re-assert the target's markers
    and drop the markers WE authored in other versions, while preserving markers
    we never authored (handled in build_put_payload). drop_secs is the union of
    every version's marker in-seconds minus the target's, so only our own
    later/other additions are removed. Fields/notes overwrite to the target's
    values and are not cleared when absent (never destroy a foreign value).
    """
    snap = target.snapshot
    desired: list[Marker] = []
    target_secs: set[float] = set()
    for m in snap.get("markers") or []:
        if isinstance(m, dict):
            # fps=0.0 sentinel: the frame is derived from the clip's REAL fps in
            # build_put_payload, never a hardcoded value.
            mm = _marker_from_review_value(m, 0.0)
            if mm is not None:
                desired.append(mm)
                s = (m.get("in") or {}).get("secs")
                if isinstance(s, (int, float)):
                    target_secs.add(float(s))

    ours_all: set[float] = set()
    for v in versions:
        for m in v.snapshot.get("markers") or []:
            if isinstance(m, dict):
                s = (m.get("in") or {}).get("secs")
                if isinstance(s, (int, float)):
                    ours_all.add(float(s))
    drop_secs = tuple(sorted(ours_all - target_secs))

    ops: list[ChangeOp] = []
    # Always emit when there is something to assert OR drop, so switching to a
    # marker-less version still strips our later additions.
    if desired or drop_secs:
        ops.append(ReconcileMarkers(desired=tuple(desired), drop_secs=drop_secs))
    for ident, val in (snap.get("fields") or {}).items():
        ops.append(SetField(identifier=ident, value=val))
    if snap.get("notes"):
        ops.append(ReplaceNote(target="notes", text=str(snap["notes"])))
    if snap.get("bigNotes"):
        ops.append(ReplaceNote(target="bigNotes", text=str(snap["bigNotes"])))
    return ops


def _materialize(base: dict[str, Any], accepted: list, *, fps: float) -> dict[str, Any]:
    """Lay accepted items on top of the base snapshot: markers add, fields set,
    notes/bigNotes set. Mirrors the op semantics in WriteQueue._items_to_change_ops."""
    markers = list(base.get("markers") or [])
    fields = dict(base.get("fields") or {})
    notes = base.get("notes")
    big_notes = base.get("bigNotes")
    for it in accepted:
        value = it.edited_value if it.edited_value is not None else it.proposed_value
        if it.kind == "marker" and isinstance(value, dict):
            markers.append(value)
        elif it.kind == "field" and it.target_identifier:
            fields[it.target_identifier] = (
                value.get("value") if isinstance(value, dict) and "value" in value else value
            )
        elif it.kind == "note" and it.target_identifier:
            text = (
                str(value.get("value"))
                if isinstance(value, dict) and "value" in value
                else str(value)
            )
            if it.target_identifier == "bigNotes":
                big_notes = text
            else:
                notes = text
    return {"markers": markers, "fields": fields, "notes": notes, "bigNotes": big_notes}


def _diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Coarse delta for the History UI: added markers count + changed fields +
    whether notes changed. Best-effort provenance, not a merge base."""
    b_fields, a_fields = before.get("fields") or {}, after.get("fields") or {}
    changed = {k: a_fields[k] for k in a_fields if a_fields.get(k) != b_fields.get(k)}
    return {
        "markers_added": max(0, len(after.get("markers") or []) - len(before.get("markers") or [])),
        "fields_changed": changed,
        "notes_changed": (before.get("notes") != after.get("notes")),
        "big_notes_changed": (before.get("bigNotes") != after.get("bigNotes")),
    }
