from __future__ import annotations

from pathlib import Path

from backend.app.archive.ai_store_model import (
    AIStoreCapabilities,
    StoreHealth,
    UploadedRef,
)
from backend.app.archive.model import ClipKey


class GeminiFilesInputStore:
    """Stub adapter for Google Gemini Files API.

    Defined here to prove the AIInputStore Protocol compiles against a
    non-GCS shape. Not wired up in PR 2; methods raise NotImplementedError.
    A later PR will implement this for installs that prefer not to manage
    a GCS bucket.
    """

    id = "gemini-files"
    capabilities = AIStoreCapabilities(
        persistent=False,
        dedup_by_sha256=False,
        max_file_bytes=2 * 1024 * 1024 * 1024,  # 2 GB, per Files API docs
    )

    async def ensure_uploaded(
        self, clip_key: ClipKey, local_path: Path, mime: str
    ) -> UploadedRef:
        raise NotImplementedError(
            "GeminiFilesInputStore is a stub; wire it in a follow-on PR."
        )

    async def status(self, clip_key: ClipKey) -> UploadedRef | None:
        raise NotImplementedError

    async def evict(self, clip_key: ClipKey) -> None:
        raise NotImplementedError

    async def health(self) -> StoreHealth:
        raise NotImplementedError

    async def reference_for_gemini(self, ref: UploadedRef) -> dict:
        return {"file_data": {"file_id": ref.handle}}
