# backend/app/services/restore_service.py
"""RestoreService — load a published clip_version's snapshot back into the
working draft as fresh, pending review_items. Publishing it forward creates a
NEW version (origin='restore'); history is never mutated. See spec §Restore."""

from __future__ import annotations

import aiosqlite

from backend.app.models.annotation import ReviewItem


class RestoreService:
    def __init__(self, *, clip_versions_repo, review_items_repo, annotations_repo):
        self._versions = clip_versions_repo
        self._review_items = review_items_repo
        self._annotations = annotations_repo

    async def restore_into_draft(
        self, conn: aiosqlite.Connection, *, clip_id: int, version_num: int
    ) -> int:
        versions = await self._versions.list_by_clip(conn, clip_id)
        target = next((v for v in versions if v.version_num == version_num), None)
        if target is None:
            raise LookupError(f"clip {clip_id} has no version {version_num}")

        annotation_id = target.annotation_id
        if annotation_id is None:
            anns = await self._annotations.list_by_clip(conn, clip_id)
            annotation_id = anns[0].id if anns else None
        if annotation_id is None:
            raise LookupError(f"clip {clip_id} has no annotation to anchor a restore")

        await self._review_items.clear_unapplied_for_clip(conn, clip_id)
        items = _snapshot_to_items(target.snapshot, clip_id, annotation_id)
        inserted = await self._review_items.bulk_insert(conn, items)
        return len(inserted)


def _snapshot_to_items(snapshot, clip_id, annotation_id) -> list[ReviewItem]:
    items: list[ReviewItem] = []
    for m in snapshot.get("markers") or []:
        items.append(ReviewItem(annotation_id=annotation_id, studio_run_id=None,
                                catdv_clip_id=clip_id, kind="marker",
                                target_identifier=None, proposed_value=m))
    for ident, val in (snapshot.get("fields") or {}).items():
        items.append(ReviewItem(annotation_id=annotation_id, studio_run_id=None,
                                catdv_clip_id=clip_id, kind="field",
                                target_identifier=ident, proposed_value=val))
    if snapshot.get("notes"):
        items.append(ReviewItem(annotation_id=annotation_id, studio_run_id=None,
                                catdv_clip_id=clip_id, kind="note",
                                target_identifier="notes", proposed_value=snapshot["notes"]))
    if snapshot.get("bigNotes"):
        items.append(ReviewItem(annotation_id=annotation_id, studio_run_id=None,
                                catdv_clip_id=clip_id, kind="note",
                                target_identifier="bigNotes", proposed_value=snapshot["bigNotes"]))
    return items
