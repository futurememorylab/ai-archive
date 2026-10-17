"""The canonical cache-layer sequence for getting a clip's media to Gemini.

Clip media never comes straight from CatDV or GCS. It goes through the cache
layers in order (invariant 4):

1. AI-input store (`AIInputStore.status`): already uploaded? Use it. This
   needs no local file and no archive call.
2. Proxy cache (`ProxyResolver.path_for_clip_id`): a local copy, downloaded
   from the archive only when the resolver can reach it. A miss raises
   `ProxyNotFound`, which names the layer; the caller degrades (marks the
   item failed with a readable message) instead of crashing.
3. Back to the AI-input store (`ensure_uploaded`) with that local file.

The thumbnail cache (`ThumbnailService.get_or_fetch`) is the third layer and
follows the same shape for poster frames; Gemini never needs it.

Callers that track progress (job item status, the prefetch queue row) pass a
`CacheProgress`; it is only touched on an AI-store miss.
"""

from __future__ import annotations

import mimetypes
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from backend.app.archive.ai_store import AIInputStore
    from backend.app.archive.ai_store_model import UploadedRef
    from backend.app.archive.model import ClipKey
    from backend.app.services.catdv_client import ProgressCb
    from backend.app.services.proxy_resolver import ProxyResolver

DEFAULT_MIME = "video/quicktime"


class CacheProgress(Protocol):
    """Hooks a caller gets on an AI-store miss, to report what is happening."""

    async def missed(self) -> ProgressCb | None:
        """The AI store missed; about to resolve the proxy. May return a download tracker."""
        ...

    async def uploading(self) -> None:
        """The proxy resolved; about to upload it to the AI store."""
        ...


async def ai_input_for_clip(
    clip_key: ClipKey,
    clip_id: int,
    *,
    ai_store: AIInputStore,
    proxy_resolver: ProxyResolver,
    progress: CacheProgress | None = None,
) -> UploadedRef:
    """Return the clip's AI-store reference, filling the cache layers on a miss.

    Raises `ProxyNotFound` when the clip is in neither the AI store nor the
    proxy cache (and the resolver can't fetch it). Upload errors propagate
    unchanged.
    """
    upload = await ai_store.status(clip_key)
    if upload is not None:
        return upload
    tracker = await progress.missed() if progress is not None else None
    local_path = await proxy_resolver.path_for_clip_id(clip_id, tracker)
    if progress is not None:
        await progress.uploading()
    mime = mimetypes.guess_type(str(local_path))[0] or DEFAULT_MIME
    return await ai_store.ensure_uploaded(clip_key, local_path, mime)
