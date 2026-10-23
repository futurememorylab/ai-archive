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

from backend.app.archive.model import AI_OWNED_FIELDS, ReplaceAIMarkers, ReplaceNote, SetField
from backend.app.models.annotation import ClipVersion
from backend.app.services.write_queue import (
    ai_marker_values,
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
        all_accepted = await self._review_items.list_by_clip(conn, clip_id, decision="accepted")
        all_accepted = [it for it in all_accepted if it.annotation_id is not None]
        accepted = [it for it in all_accepted if it.applied_at is None]
        if not accepted:
            return None

        annotation = await self._annotations.get(conn, accepted[0].annotation_id)
        version = await self._prompts.get_version(conn, annotation.prompt_version_id)
        fps = fps_from_snapshot(annotation.clip_snapshot)

        parent = await self._versions.live_for_clip(conn, clip_id)
        base = dict(parent.snapshot) if parent is not None else await self._load_live(conn, clip_id)
        snapshot = _materialize(
            base, accepted, fps=fps, ai_markers=ai_marker_values(all_accepted, annotation.id)
        )

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
            # All accepted items: the queue skips already-applied ones but needs
            # them to build the full AI marker set (#123 review fix 2).
            accepted=all_accepted,
            target_map=version.target_map,
            expected_etag=etag_from_snapshot(annotation.clip_snapshot),
            annotation_id=annotation.id,
            fps=fps,
            clip_version_id=version_id,
        )
        return version_id

    async def remove_ai(
        self,
        conn: aiosqlite.Connection,
        *,
        clip_id: int,
        author: str | None,
    ) -> int:
        """Remove the clip's AI annotations from the archive so it can be
        annotated again from scratch. Returns the new version's id.

        Queues the same owned-only write a publish uses, with nothing in it: an
        empty ReplaceAIMarkers (the payload builder keeps every non-AI marker
        byte-identical and drops only `category == "AI"` ones) and an empty
        value for each AI_OWNED_FIELDS field (`""` clears a CatDV field,
        verified live 2026-10-14). Human markers, the human description and
        every other field are never in the write (ADR 0127).

        The removal is recorded as a version with origin 'removed' so history
        keeps it and the next publish starts from it; once it is live the clip
        reads "Not annotated". The clip's unpublished draft is discarded too.
        """
        parent = await self._versions.live_for_clip(conn, clip_id)
        base = dict(parent.snapshot) if parent is not None else await self._load_live(conn, clip_id)
        fields = {k: v for k, v in (base.get("fields") or {}).items() if k not in AI_OWNED_FIELDS}
        snapshot = {**base, "markers": [], "fields": fields}
        annotations = await self._annotations.list_by_clip(conn, clip_id)

        version_id = await self._versions.insert(
            conn,
            ClipVersion(
                catdv_clip_id=clip_id,
                version_num=await self._versions.next_version_num(conn, clip_id),
                parent_version_id=parent.id if parent else None,
                snapshot=snapshot,
                diff=_diff(base, snapshot),
                origin="removed",
                # The newest run this removal clears: a later run ends the reset.
                annotation_id=annotations[0].id if annotations else None,
                author=author,
                publish_state="publishing",
            ),
            commit=False,
        )
        ops: list = [ReplaceAIMarkers(desired=())]
        # Both shapes the summary can have been written with: a field (SetField)
        # or a note in replace mode. On CatDV both land in fields[target].
        for target in AI_OWNED_FIELDS:
            ops += [SetField(identifier=target, value=""), ReplaceNote(target=target, text="")]
        await self._wq.enqueue_ops(
            conn,
            clip_key=("catdv", str(clip_id)),
            ops=ops,
            clip_version_id=version_id,
            commit=False,
        )
        await self._review_items.discard_draft_for_clip(conn, clip_id, commit=False)
        await conn.commit()
        return version_id


def _materialize(
    base: dict[str, Any],
    accepted: list,
    *,
    fps: float,
    ai_markers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Lay accepted items on top of the base snapshot: fields set, notes kept
    under their REAL target. Mirrors WriteQueue._items_to_change_ops.

    Markers mirror ReplaceAIMarkers: when this publish carries marker items the
    snapshot's markers BECOME `ai_markers` (the full AI set CatDV receives);
    otherwise no marker op is sent and the parent's markers carry over.

    Only `notes` and `bigNotes` are CatDV's top-level note properties; every
    other note target (e.g. the summary `pragafilm.popis.ai`) is a user field
    and is stored under `fields[target]`, not collapsed into the top-level
    `notes`. Collapsing it was the bug that wrote the AI summary into the human
    `notes` on Make live / Restore (spec Problem §bug)."""
    markers = list(base.get("markers") or [])
    fields = dict(base.get("fields") or {})
    notes = base.get("notes")
    big_notes = base.get("bigNotes")
    new_markers = []
    for it in accepted:
        value = it.edited_value if it.edited_value is not None else it.proposed_value
        if it.kind == "marker" and isinstance(value, dict):
            new_markers.append(value)
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
            elif it.target_identifier == "notes":
                notes = text
            else:
                fields[it.target_identifier] = text
    if new_markers:
        markers = list(ai_markers) if ai_markers else new_markers
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
