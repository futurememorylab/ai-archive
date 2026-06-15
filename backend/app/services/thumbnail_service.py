"""ThumbnailService — resolve a clip's poster image and cache it on disk.

Mirrors the proxy-cache pattern: poster JPEGs live as plain files at
`cache_dir/{clip_id}.jpg`. The poster id comes from the clip's cached
metadata (`posterID`, falling back to the first `thumbnailIDs` entry).
When offline (`catdv is None` OR `is_online_provider()` is False) only
already-cached files are served — no network attempts.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from backend.app.media_kind import is_image_path
from backend.app.uploaded_ids import is_uploaded

if TYPE_CHECKING:
    from backend.app.archive.provider import ArchiveProvider
    from backend.app.services.catdv_client import CatdvClient
    from backend.app.services.thumbnail_store import ThumbnailStore

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
        is_online_provider: Callable[[], bool] | None = None,
        metadata_cached_provider: (
            Callable[[int], bool] | Callable[[int], Awaitable[bool]] | None
        ) = None,
        durable_store: ThumbnailStore | None = None,
        poster_id_provider: Callable[[int], Awaitable[int | None]] | None = None,
        download_concurrency: int = 3,
    ) -> None:
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._archive = archive
        self._catdv = catdv
        # When set, gates every network fetch — same pattern as the proxy
        # resolver. None means "always online if catdv is set" (used by
        # tests that don't wire a connection monitor).
        self._is_online = is_online_provider
        # When set and returns False, suppress the network step entirely —
        # without a clip_cache row we can't know posterID, so calling
        # CatDV just wastes a 60 s timeout per orphan thumb on /cache.
        self._metadata_cached = metadata_cached_provider
        # Durable GCS-backed tier (cloud only). When set, a /data miss falls
        # through to GCS *before* giving up — and GCS access is NOT gated by
        # the CatDV is_online() closure, so cached thumbs serve offline and
        # across restarts. None in local/dev mode → behavior unchanged.
        self._durable = durable_store
        # Fallback poster-id source for clips that are listed but not in
        # clip_cache: lets the thumb fetch proceed without get_clip (ADR 0072).
        self._poster_id_provider = poster_id_provider
        # Bound concurrent CatDV thumbnail downloads so a list-load's burst of
        # <img> requests doesn't stampede the seat-limited server.
        self._download_sem = asyncio.Semaphore(download_concurrency)

    def path_for(self, clip_id: int) -> Path:
        return self._cache_dir / f"{clip_id}.jpg"

    async def get_or_fetch(self, clip_id: int) -> Path | None:
        dest = self.path_for(clip_id)
        if dest.exists() and dest.stat().st_size > 0:  # sync-io-ok: pre-existing, tracked for the tier-4 async-io pass
            return dest
        if self._durable is not None and await self._durable.get(clip_id, dest):
            # GCS hit — works even when CatDV is offline or this is an upload.
            return dest
        if is_uploaded(clip_id):
            # Uploaded posters are pre-stored at path_for(clip_id) during
            # ingest. A miss is terminal — render the placeholder, never
            # consult CatDV (uploaded clips have no archive record).
            return None
        if self._catdv is None:
            return None
        if self._is_online is not None and not self._is_online():
            # Offline: cache miss is terminal — no network attempts.
            return None
        if self._metadata_cached is not None:
            result = self._metadata_cached(clip_id)
            if inspect.isawaitable(result):
                result = await result
            if not result:
                # No clip_cache row. Try the lightweight poster cache populated
                # when listing — gives posterID without a get_clip (ADR 0072).
                return await self._fetch_via_poster_cache(clip_id, dest)

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
            return await self._build_image_poster(clip_id, clip, dest)

        return await self._download_and_store(int(thumb_id), clip_id, dest)

    async def _fetch_via_poster_cache(self, clip_id: int, dest: Path) -> Path | None:
        if self._poster_id_provider is None:
            return None
        poster_id = await self._poster_id_provider(clip_id)
        if not poster_id:
            return None
        return await self._download_and_store(int(poster_id), clip_id, dest)

    async def _download_and_store(self, thumb_id: int, clip_id: int, dest: Path) -> Path | None:
        async with self._download_sem:
            try:
                await self._catdv.download_thumbnail(thumb_id, dest)
            except Exception as exc:  # noqa: BLE001 — transport / auth / 404
                _log.debug("thumb: download(%s) failed: %s", clip_id, exc)
                if dest.exists() and dest.stat().st_size == 0:  # sync-io-ok: pre-existing, tracked for the tier-4 async-io pass
                    dest.unlink(missing_ok=True)  # sync-io-ok: pre-existing, tracked for the tier-4 async-io pass
                return None
        if dest.exists() and dest.stat().st_size > 0:  # sync-io-ok: pre-existing, tracked for the tier-4 async-io pass
            if self._durable is not None:
                await self._durable.put(clip_id, dest)
            return dest
        return None

    async def evict(self, clip_id: int) -> None:
        """Remove a clip's poster from the local cache and the durable store.

        Best-effort and offline-safe: a missing local file is not an error,
        and a durable-store failure (GCS unreachable) is swallowed by the
        store wrapper so local cleanup still completes. Called by the upload
        orphan-GC when an uploaded clip leaves its last set."""
        dest = self.path_for(clip_id)
        await asyncio.to_thread(dest.unlink, missing_ok=True)
        if self._durable is not None:
            await self._durable.delete(clip_id)

    async def push_durable(self, clip_id: int, src: Path) -> None:
        """Mirror a poster already written to /data into the durable GCS
        store. Called by the studio upload-ingest path after writing the
        local copy. No-op when no durable store is wired (local/dev mode)."""
        if self._durable is not None:
            await self._durable.put(clip_id, src)

    async def _build_image_poster(self, clip_id: int, clip, dest: Path) -> Path | None:
        """For a still with no CatDV poster: fetch the original and downscale
        it to a cached JPEG poster. Returns None (→ placeholder) for non-image
        clips or any decode failure."""
        if self._catdv is None:
            return None
        if self._is_online is not None and not self._is_online():
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
            dest.unlink(missing_ok=True)  # sync-io-ok: pre-existing, tracked for the tier-4 async-io pass
            return None
        finally:
            tmp.unlink(missing_ok=True)  # sync-io-ok: pre-existing, tracked for the tier-4 async-io pass
        if dest.exists() and dest.stat().st_size > 0:  # sync-io-ok: pre-existing, tracked for the tier-4 async-io pass
            if self._durable is not None:
                await self._durable.put(clip_id, dest)
            return dest
        return None
