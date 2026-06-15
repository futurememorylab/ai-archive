"""UploadCleanup — orphan garbage-collection for uploaded studio clips.

Removing an uploaded clip from a set only deletes the membership row. The
upload itself (its `uploaded_clip` row, the local cached video + poster,
and — in cloud mode — the GCS blob + `ai_store_files` row) must be GC'd
once the clip is referenced by **zero** sets, otherwise each
delete+reupload leaks the file size on the seat-/disk-constrained box and
on ephemeral Cloud Run. See issue #57 (follow-up to #55).

The reference-count rule keeps a clip that still lives in another set
untouched. All byte removal routes through the existing cache layers
(CLAUDE.md: never raw deletes):

* proxy bytes + ai-store blob → ``CacheActions.evict_clip_everywhere``
  (which unlinks the local file, calls ``ai_store.evict()``, prunes the
  index rows, and is offline-safe — a failed GCS evict leaves the
  ``ai_store_files`` row in place to be retried by orphan GC, and never
  blocks the local cleanup),
* poster → ``ThumbnailService.evict`` (local JPEG + durable GCS thumb),
* ``uploaded_clip`` row → ``UploadedClipsRepo.delete``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

import aiosqlite

from backend.app.uploaded_ids import is_uploaded

if TYPE_CHECKING:
    from backend.app.repositories.studio_sets import StudioSetsRepo
    from backend.app.repositories.uploaded_clips import UploadedClipsRepo
    from backend.app.services.cache_actions import CacheActions
    from backend.app.services.thumbnail_service import ThumbnailService

log = logging.getLogger(__name__)


class UploadCleanup:
    def __init__(
        self,
        *,
        db_provider: Callable[[], aiosqlite.Connection],
        studio_sets_repo: StudioSetsRepo,
        uploaded_clips_repo: UploadedClipsRepo,
        cache_actions: CacheActions,
        thumbnail_service: ThumbnailService | None = None,
    ) -> None:
        self._db_provider = db_provider
        self._sets_repo = studio_sets_repo
        self._uploads_repo = uploaded_clips_repo
        self._cache_actions = cache_actions
        self._thumbnail_service = thumbnail_service

    async def gc_if_orphaned(self, clip_id: int) -> bool:
        """GC an uploaded clip iff it is now referenced by zero sets.

        Returns True when the clip was garbage-collected, False when it was
        left alone (not an upload, or still a member of another set). Call
        *after* the set-membership row has been removed.
        """
        if not is_uploaded(clip_id):
            return False
        db = self._db_provider()
        if await self._sets_repo.count_sets_for_clip(db, clip_id) > 0:
            return False

        key = ("uploaded", str(clip_id))
        await self._cache_actions.evict_clip_everywhere(key, force=True, who="upload-gc")
        if self._thumbnail_service is not None:
            await self._thumbnail_service.evict(clip_id)
        await self._uploads_repo.delete(db, clip_id)
        log.info("upload-gc: removed orphaned uploaded clip %s", clip_id)
        return True
