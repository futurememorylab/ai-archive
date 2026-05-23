"""ArchiveProvider Protocol — the port that every backend (CatDV, filesystem)
must satisfy. Defines list/get/write/health surface plus capability/health
value objects."""

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


@dataclass(frozen=True)
class ProviderHealth:
    ok: bool
    latency_ms: float | None = None
    detail: str | None = None


@runtime_checkable
class ArchiveProvider(Protocol):
    id: ProviderId = ""
    capabilities: ProviderCapabilities = None  # type: ignore[assignment]

    async def health(self) -> ProviderHealth: ...
    async def list_clips(self, catalog: str, query: ClipQuery) -> ClipPage: ...
    async def get_clip(self, clip: ProviderClipId) -> CanonicalClip: ...
    async def list_field_definitions(self) -> list[FieldDef]: ...
    async def apply_changes(self, change_set: ChangeSet) -> WriteResult: ...
