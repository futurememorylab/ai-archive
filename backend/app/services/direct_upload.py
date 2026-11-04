"""Direct-to-bucket uploads for Prompt Studio (cloud only).

On Cloud Run an upload sent through the app is refused above 32 MiB (the
platform's HTTP/1 request limit), and the bytes would sit in the instance's
in-memory /data. In ai_store mode the browser instead PUTs the file straight
into the clip's GCS blob through a short-lived signed URL, then tells the app
it finished. The app checks the blob is really there with the expected size
and records it in `ai_store_files`, the same row `GcsInputStore.ensure_uploaded`
writes, so playback (`AiStoreBackend.locate`) and annotation
(`clip_media.ai_input_for_clip`, status-first) find it with no local file.

Local runs (media_cache=local) keep the multipart upload through the app:
there is no request limit there, and the local copy backs playback.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

#: How long a browser has to start its PUT. A signed URL is checked when the
#: request starts, so a slow 500 MB upload that began in time still finishes.
UPLOAD_URL_TTL_S = 15 * 60


class UploadNotReceived(Exception):
    """The bucket doesn't hold the file the browser said it sent."""


class DirectUpload:
    """Signs browser uploads into the AI store's bucket and registers them."""

    def __init__(
        self,
        *,
        gcs: Any,  # GcsService, duck-typed for tests
        files_repo: Any,  # AIStoreFilesRepo
        store_id: str,
        db_provider: Callable[[], Any],
    ) -> None:
        self._gcs = gcs
        self._repo = files_repo
        self._store_id = store_id
        self._db_provider = db_provider

    async def upload_url(self, clip_id: int, mime: str) -> str:
        """A signed URL the browser PUTs the clip's bytes to, with `mime` as
        its Content-Type."""
        return await asyncio.to_thread(
            self._gcs.signed_upload_url, clip_id, mime, expires_s=UPLOAD_URL_TTL_S
        )

    async def complete(self, clip_id: int, *, mime: str, size_bytes: int) -> None:
        """Check the blob arrived whole and register it as the clip's AI-store
        copy. Raises UploadNotReceived when it is missing or its size differs
        from what the browser announced (which is also what the size cap was
        checked against)."""
        meta = await asyncio.to_thread(self._gcs.blob_meta, clip_id)
        if meta is None:
            raise UploadNotReceived("the file never reached storage")
        if meta.size != size_bytes:
            raise UploadNotReceived(
                f"storage holds {meta.size} bytes but {size_bytes} were expected"
            )
        await self._repo.upsert(
            self._db_provider(),
            store_id=self._store_id,
            clip_id=clip_id,
            gcs_uri=self._gcs.gs_uri(clip_id),
            mime_type=mime,
            size_bytes=meta.size,
            # No local file to hash; the column only dedups re-uploads of a
            # local file, which a direct upload never has.
            sha256=f"md5:{meta.md5 or ''}",
            provider_id="uploaded",
            provider_clip_id=str(clip_id),
        )

    async def discard(self, clip_id: int) -> None:
        """Delete whatever the browser managed to send (missing counts as done)."""
        await asyncio.to_thread(self._gcs.delete, clip_id)


def build_direct_upload(
    settings: Any, *, gcs: Any, ai_store: Any, files_repo: Any, db_provider: Callable[[], Any]
) -> DirectUpload | None:
    """A DirectUpload when the app keeps media in a GCS AI store (cloud);
    None otherwise, which makes the browser upload through the app."""
    store_id = str(getattr(ai_store, "id", ""))
    if settings.media_cache != "ai_store" or not store_id.startswith("gcs:"):
        return None
    return DirectUpload(gcs=gcs, files_repo=files_repo, store_id=store_id, db_provider=db_provider)
