from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from backend.app.archive.model import (
    CanonicalClip,
    ChangeSet,
    ClipPage,
    ClipQuery,
    FieldDef,
    ProviderClipId,
    ProviderId,
    WriteResult,
)


@dataclass(frozen=True)
class ProviderCapabilities:
    supports_markers: bool
    supports_notes: frozenset[str]
    supports_field_create: bool
    supports_etag: bool
    media_is_local: bool
    write_atomicity: Literal["per-clip", "per-op"]


@runtime_checkable
class ArchiveProvider(Protocol):
    id: ProviderId = ""
    capabilities: ProviderCapabilities = None  # type: ignore[assignment]

    async def list_clips(self, catalog: str, query: ClipQuery) -> ClipPage: ...
    async def get_clip(self, clip: ProviderClipId) -> CanonicalClip: ...
    async def list_field_definitions(self) -> list[FieldDef]: ...
    async def apply_changes(self, change_set: ChangeSet) -> WriteResult: ...
