"""AIInputStore port — protocol for stores that hand media bytes to Gemini.

Implemented by `ai_stores/gcs/adapter.py` and `ai_stores/gemini_files/adapter.py`;
selected at startup by `ai_stores/registry.py`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from backend.app.archive.ai_store_model import (
    AIStoreCapabilities,
    StoreHealth,
    UploadedRef,
)
from backend.app.archive.model import ClipKey


@runtime_checkable
class AIInputStore(Protocol):
    """Port: where Gemini reads media bytes from.

    Implementations are responsible for (a) putting a copy of the local file
    somewhere Vertex AI Gemini can read it, (b) producing the SDK-shaped
    fragment that `generate_content()` accepts, and (c) tracking the upload
    in their own persistent index.
    """

    id: str = ""
    capabilities: AIStoreCapabilities = None  # type: ignore[assignment]

    async def ensure_uploaded(
        self, clip_key: ClipKey, local_path: Path, mime: str
    ) -> UploadedRef: ...

    async def status(self, clip_key: ClipKey) -> UploadedRef | None: ...

    async def evict(self, clip_key: ClipKey) -> None: ...

    async def health(self) -> StoreHealth: ...

    async def reference_for_gemini(self, ref: UploadedRef) -> dict: ...
