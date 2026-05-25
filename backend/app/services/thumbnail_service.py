"""ThumbnailService — resolve a clip's poster image and cache it on disk.

Mirrors the proxy-cache pattern: poster JPEGs live as plain files at
`cache_dir/{clip_id}.jpg`. The poster id comes from the clip's cached
metadata (`posterID`, falling back to the first `thumbnailIDs` entry).

Still-image clips (e.g. scanned photos) carry no `posterID`/`thumbnailIDs`
in CatDV, so for those we fall back to the clip's media file itself when it
is a web-displayable image. When offline (`catdv is None`) only
already-cached files are served.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.app.archive.provider import ArchiveProvider
    from backend.app.services.catdv_client import CatdvClient

_log = logging.getLogger(__name__)

# Media extensions a browser can render directly in an <img>. Used to decide
# whether a poster-less clip's media file can itself serve as the thumbnail.
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp")


class ThumbnailService:
    def __init__(
        self,
        *,
        cache_dir: Path,
        archive: ArchiveProvider,
        catdv: CatdvClient | None = None,
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

        try:
            if thumb_id:
                await self._catdv.download_thumbnail(int(thumb_id), dest)
            elif _media_is_image(clip.provider_data):
                # No CatDV poster/thumbnail (still-image clips have none), but
                # the media file is itself a web image — serve it directly.
                await self._catdv.download_proxy(clip_id, dest)
            else:
                return None
        except Exception as exc:  # noqa: BLE001 — transport / auth / 404
            _log.debug("thumb: download(%s) failed: %s", clip_id, exc)
            if dest.exists() and dest.stat().st_size == 0:
                dest.unlink(missing_ok=True)
            return None

        if dest.exists() and dest.stat().st_size > 0:
            return dest
        return None


def _media_is_image(provider_data: dict) -> bool:
    """True when the clip's media file is a browser-displayable image."""
    media = provider_data.get("media") or {}
    file_path = (media.get("filePath") or "").lower()
    return file_path.endswith(_IMAGE_EXTS)
