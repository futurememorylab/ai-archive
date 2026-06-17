# backend/app/services/publish_service.py
"""PublishService — turns a clip's accepted working draft into an immutable
clip_versions row and drives it through the existing write queue.

Flow (see docs/specs/2026-06-17-clip-version-history-design.md §Publish):
  1. resolve accepted, annotation-bound review_items
  2. materialize the FULL committed snapshot (current live/CatDV state + accepted)
  3. insert clip_versions row (publishing) + diff vs the live parent
  4. enqueue ops via WriteQueue, stamped with the version id, plus one
     `SetField pragafilm.anno_version` provenance op
  5. mark items applied (done inside the write queue)

The SyncEngine flips the row live (Task 7) when CatDV confirms.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from backend.app.archive.model import SetField
from backend.app.models.annotation import ClipVersion
from backend.app.services.write_queue import etag_from_snapshot, fps_from_snapshot

PROVENANCE_FIELD = "pragafilm.anno_version"
SnapshotLoader = Callable[[aiosqlite.Connection, int], Awaitable[dict[str, Any]]]


def build_provenance_value(
    *, version_num: int, author: str | None, model: str | None, ts: str
) -> str:
    return f"#{version_num} · {author or '—'} · {ts} · {model or '—'}"


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
        ts = datetime.now(UTC).isoformat()
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

        provenance = SetField(
            identifier=PROVENANCE_FIELD,
            value=build_provenance_value(
                version_num=num, author=author, model=annotation.model, ts=ts
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
            extra_ops=[provenance],
        )
        return version_id


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
