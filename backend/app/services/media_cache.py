"""MediaCacheBackend -- the single authority for proxy-media caching and
playback location. Two backends, selected by settings.media_cache:

- LocalProxyBackend (dev): download to the local proxy cache, serve from
  disk, GCS signed URL as read fallback (today's behavior).
- AiStoreBackend (cloud): cache writes upload to the AI store (GCS) and
  the local staging file is deleted; playback is a signed URL. The local
  proxy cache is never used for reads.

ensure_cached() may need CatDV (the tunnel) on a miss; locate() never
does -- it depends only on the store index + URL signing, so cached
clips stay playable when CatDV is offline.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
from pathlib import Path
from typing import Protocol

from backend.app.archive.model import ClipKey
from backend.app.services.media_locator import (
    SIGNED_URL_TTL_S,
    LocalFile,
    MediaLocator,
    MediaNotAvailable,
    RemoteUrl,
)
from backend.app.uploaded_ids import is_uploaded

log = logging.getLogger(__name__)

_DEFAULT_MIME = "video/quicktime"


def _clip_key(clip_id: int) -> ClipKey:
    return ("uploaded" if is_uploaded(clip_id) else "catdv", str(clip_id))


class MediaCacheBackend(Protocol):
    async def ensure_cached(self, clip_id: int) -> None: ...

    async def locate(self, clip_id: int) -> LocalFile | RemoteUrl | None: ...


class LocalProxyBackend:
    """Dev backend: local proxy cache first, GCS signed URL as fallback."""

    def __init__(self, *, resolver, ai_store, gcs) -> None:
        self._resolver = resolver
        self._locator = MediaLocator(
            proxy_resolver=resolver,
            ai_store=ai_store,
            gcs_service=gcs,
            prefer="local",
        )

    async def ensure_cached(self, clip_id: int) -> None:
        await self._resolver.path_for_clip_id(clip_id)

    async def locate(self, clip_id: int) -> LocalFile | RemoteUrl | None:
        try:
            return await self._locator.locate(clip_id)
        except MediaNotAvailable:
            return None


class AiStoreBackend:
    """Cloud backend: cache writes upload to the AI store (GCS); the local
    staging file is deleted after upload. Playback is a signed URL. The
    local proxy cache is never consulted for reads."""

    def __init__(
        self, *, rest_resolver, ai_store, gcs, proxy_cache_repo, db_provider
    ) -> None:
        self._resolver = rest_resolver
        self._ai_store = ai_store
        self._gcs = gcs
        self._proxy_cache_repo = proxy_cache_repo
        self._db_provider = db_provider

    async def ensure_cached(self, clip_id: int) -> None:
        key = _clip_key(clip_id)
        if await self._ai_store.status(key) is not None:
            return  # already in GCS -- no tunnel hit (status-first fast-path)

        path: Path = await self._resolver.path_for_clip_id(clip_id)
        try:
            mime = mimetypes.guess_type(str(path))[0] or _DEFAULT_MIME
            await self._ai_store.ensure_uploaded(key, path, mime)
        finally:
            # Cleanup is best-effort: keep peak ephemeral-disk usage to a
            # single proxy (drop the staging file + its proxy_cache row,
            # even on upload failure), but never let a cleanup error mask
            # the real download/upload exception that's propagating.
            try:
                await asyncio.to_thread(path.unlink, True)  # missing_ok=True
            except OSError:
                log.warning("could not delete staging file %s", path, exc_info=True)
            try:
                await self._proxy_cache_repo.delete(self._db_provider(), clip_id)
            except Exception:  # noqa: BLE001 -- best-effort cleanup, must not mask
                log.warning("could not delete proxy_cache row for clip %s", clip_id, exc_info=True)

    async def locate(self, clip_id: int) -> LocalFile | RemoteUrl | None:
        ref = await self._ai_store.status(_clip_key(clip_id))
        if ref is None or not ref.handle.startswith("gs://"):
            return None
        url = await asyncio.to_thread(
            self._gcs.signed_url, ref.handle, expires_s=SIGNED_URL_TTL_S
        )
        return RemoteUrl(url)
