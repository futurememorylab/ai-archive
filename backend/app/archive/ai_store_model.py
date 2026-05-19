from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class UploadedRef:
    """Where an AI input store put a copy of a clip's media bytes."""

    handle: str
    mime_type: str
    size_bytes: int
    sha256: str
    uploaded_at: datetime
    expires_at: datetime | None = None


@dataclass(frozen=True)
class AIStoreCapabilities:
    persistent: bool
    dedup_by_sha256: bool
    max_file_bytes: int


@dataclass(frozen=True)
class StoreHealth:
    ok: bool
    detail: str | None = None
