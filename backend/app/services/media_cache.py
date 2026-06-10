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

import logging
import mimetypes
from pathlib import Path
from typing import Protocol

from backend.app.archive.model import ClipKey
from backend.app.services.media_locator import (
    LocalFile,
    MediaLocator,
    MediaNotAvailable,
    RemoteUrl,
    SIGNED_URL_TTL_S,
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
