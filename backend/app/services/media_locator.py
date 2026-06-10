"""MediaLocator -- decides where playback bytes for a clip come from.

Consults the two existing cache layers through their own interfaces
(ProxyResolver for the local proxy cache, AIInputStore for GCS) in the
order given by the ``prefer`` argument the locator is constructed with
(``"local"`` tries the proxy cache first; ``"gcs"`` tries GCS first).
The argument is a preference order, not an exclusive mode: both layers are always tried
before giving up, and a both-miss raises ``MediaNotAvailable`` naming
what each layer said (CLAUDE.md: errors must name WHICH cache layer
missed). The locator never fetches and never talks to CatDV/GCS
directly -- it only asks each layer "do you have it".
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from backend.app.archive.ai_store import AIInputStore
    from backend.app.services.gcs import GcsService
    from backend.app.services.proxy_resolver import ProxyResolver

SIGNED_URL_TTL_S = 3600


@dataclass(frozen=True)
class LocalFile:
    path: Path


@dataclass(frozen=True)
class RemoteUrl:
    url: str


class MediaNotAvailable(Exception):
    """Neither cache layer can serve this clip right now (transient --
    callers must NOT infer the clip is gone; see archive/errors.py)."""

    def __init__(self, clip_id: int, detail: str) -> None:
        super().__init__(f"clip {clip_id} not available: {detail}")


class MediaLocator:
    def __init__(
        self,
        *,
        proxy_resolver: "ProxyResolver | None",
        ai_store: "AIInputStore",
        gcs_service: "GcsService",
        prefer: Literal["local", "gcs"],
    ) -> None:
        self._resolver = proxy_resolver
        self._ai_store = ai_store
        self._gcs = gcs_service
        self._prefer = prefer

    async def locate(self, clip_id: int) -> LocalFile | RemoteUrl:
        attempts = (
            (self._from_local, self._from_gcs)
            if self._prefer == "local"
            else (self._from_gcs, self._from_local)
        )
        misses: list[str] = []
        for attempt in attempts:
            found = await attempt(clip_id, misses)
            if found is not None:
                return found
        raise MediaNotAvailable(clip_id, "; ".join(misses))

    async def _from_local(self, clip_id: int, misses: list[str]) -> LocalFile | None:
        if self._resolver is None:
            misses.append("local cache: resolver offline")
            return None
        try:
            return LocalFile(await self._resolver.path_for_clip_id(clip_id))
        except Exception as exc:  # fall through to the other layer, not terminal
            misses.append(f"local cache: {exc}")
            return None

    async def _from_gcs(self, clip_id: int, misses: list[str]) -> RemoteUrl | None:
        try:
            ref = await self._ai_store.status(("catdv", str(clip_id)))
        except Exception as exc:  # fall through to the other layer, not terminal
            misses.append(f"ai store: {exc}")
            return None
        if ref is None or not ref.handle.startswith("gs://"):
            misses.append("ai store: not uploaded")
            return None
        url = await asyncio.to_thread(
            self._gcs.signed_url, ref.handle, expires_s=SIGNED_URL_TTL_S
        )
        return RemoteUrl(url)
