"""ThumbnailService — resolve a clip's poster image and cache it on disk.

Mirrors the proxy-cache pattern: poster JPEGs live as plain files at
`cache_dir/{clip_id}.jpg`. The poster id comes from the clip's cached
metadata (`posterID`, falling back to the first `thumbnailIDs` entry).
When offline (`catdv is None`) only already-cached files are served.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.app.archive.provider import ArchiveProvider
    from backend.app.services.catdv_client import CatdvClient

_log = logging.getLogger(__name__)


class ThumbnailService:
    def __init__(
        self,
        *,
        cache_dir: Path,
        archive: "ArchiveProvider",
        catdv: "CatdvClient | None" = None,
    ) -> None:
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._archive = archive
        self._catdv = catdv

    def path_for(self, clip_id: int) -> Path:
        return self._cache_dir / f"{clip_id}.jpg"

    async def get_or_fetch(self, clip_id: int) -> Path | None:
        dest = self.path_for(clip_id)
        if dest.exists() and dest.stat().st_size > 0:
            return dest
        if self._catdv is None:
            return None

        try:
            clip = await self._archive.get_clip(str(clip_id))
        except Exception as exc:  # noqa: BLE001 — offline / not-found / transport
            _log.debug("thumb: get_clip(%s) failed: %s", clip_id, exc)
            return None

        thumb_id = clip.provider_data.get("posterID")
        if not thumb_id:
            ids = clip.provider_data.get("thumbnailIDs") or []
            thumb_id = ids[0] if ids else None
        if not thumb_id:
            return None

        try:
            await self._catdv.download_thumbnail(int(thumb_id), dest)
        except Exception as exc:  # noqa: BLE001 — transport / auth / 404
            _log.debug("thumb: download(%s) failed: %s", clip_id, exc)
            if dest.exists() and dest.stat().st_size == 0:
                dest.unlink(missing_ok=True)
            return None

        if dest.exists() and dest.stat().st_size > 0:
            return dest
        return None
