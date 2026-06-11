"""ThumbnailStore — durable (GCS-backed) tier beneath ThumbnailService.

ThumbnailService caches poster JPEGs on /data, which on Cloud Run is an
ephemeral tmpfs wiped on every restart. This store gives them a durable home
in GCS (thumbs/{clip_id}.jpg, same bucket as proxies) so they survive restarts
and serve while CatDV is offline — GCS is a separate network from the CatDV
tunnel. Mirrors the proxy ai_store relationship (ADR 0069)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Protocol

from backend.app.services.gcs import GcsService

log = logging.getLogger(__name__)


class ThumbnailStore(Protocol):
    async def get(self, clip_id: int, dest: Path) -> bool: ...

    async def put(self, clip_id: int, src: Path) -> None: ...


class GcsThumbnailStore:
    """GCS-backed ThumbnailStore. All blocking SDK calls run in a worker
    thread (CLAUDE.md: no sync I/O inside async def)."""

    def __init__(self, gcs: GcsService) -> None:
        self._gcs = gcs

    async def get(self, clip_id: int, dest: Path) -> bool:
        try:
            return await asyncio.to_thread(self._gcs.download_thumb, clip_id, dest)
        except Exception:  # noqa: BLE001 — transient GCS error ⇒ treat as a miss
            log.debug("thumb store: get(%s) failed", clip_id, exc_info=True)
            return False

    async def put(self, clip_id: int, src: Path) -> None:
        try:
            await asyncio.to_thread(self._gcs.upload_thumb, clip_id, src)
        except Exception:  # noqa: BLE001 — best-effort; never mask the served thumb
            log.warning("thumb store: put(%s) failed", clip_id, exc_info=True)
