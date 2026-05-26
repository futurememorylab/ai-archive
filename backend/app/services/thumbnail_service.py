"""ThumbnailService — resolve a clip's poster image and cache it on disk.

Mirrors the proxy-cache pattern: poster JPEGs live as plain files at
`cache_dir/{clip_id}.jpg`. The poster id comes from the clip's cached
metadata (`posterID`, falling back to the first `thumbnailIDs` entry).
When offline (`catdv is None`) only already-cached files are served.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from backend.app.media_kind import is_image_path

if TYPE_CHECKING:
    from backend.app.archive.provider import ArchiveProvider
    from backend.app.services.catdv_client import CatdvClient

_log = logging.getLogger(__name__)


def _downscale_to_jpeg(src: Path, dst: Path, max_edge: int) -> None:
    """Open `src`, scale so its long edge ≤ max_edge, save JPEG to `dst`.
    Synchronous (Pillow); call via asyncio.to_thread."""
    from PIL import Image

    with Image.open(src) as im:
        im = im.convert("RGB")
        im.thumbnail((max_edge, max_edge))
        im.save(dst, format="JPEG", quality=85)


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
        if not thumb_id:
            return await self._build_image_poster(clip, dest)

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

    async def _build_image_poster(self, clip, dest: Path) -> Path | None:
        """For a still with no CatDV poster: fetch the original and downscale
        it to a cached JPEG poster. Returns None (→ placeholder) for non-image
        clips or any decode failure."""
        if self._catdv is None:
            return None
        media = clip.provider_data.get("media") or {}
        file_path = media.get("filePath")
        media_id = media.get("ID")
        if not is_image_path(file_path) or media_id is None:
            return None
        tmp = dest.with_suffix(dest.suffix + ".orig")
        try:
            await self._catdv.download_original(int(media_id), tmp)
            await asyncio.to_thread(_downscale_to_jpeg, tmp, dest, 480)
        except Exception as exc:  # noqa: BLE001 — transport / decode / unsupported
            _log.debug("thumb: image poster build failed for %s: %s", dest.stem, exc)
            dest.unlink(missing_ok=True)
            return None
        finally:
            tmp.unlink(missing_ok=True)
        if dest.exists() and dest.stat().st_size > 0:
            return dest
        return None
