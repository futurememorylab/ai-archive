from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

ProviderId = str
ProviderClipId = str
ClipKey = tuple[ProviderId, ProviderClipId]


@dataclass(frozen=True)
class Timecode:
    secs: float
    fps: float
    frm: int | None = None
    txt: str | None = None


@dataclass(frozen=True)
class Marker:
    name: str
    in_: Timecode
    out: Timecode | None
    description: str | None = None
    category: str | None = None
    color: str | None = None


@dataclass(frozen=True)
class FieldValue:
    identifier: str
    value: Any
    is_multi: bool = False


@dataclass(frozen=True)
class MediaRef:
    mime_type: str
    size_bytes: int | None
    cached_path: Path | None
    upstream_handle: str


@dataclass(frozen=True)
class CanonicalClip:
    key: ClipKey
    name: str
    duration_secs: float
    fps: float
    markers: tuple[Marker, ...]
    fields: dict[str, FieldValue]
    notes: dict[str, str]
    media: MediaRef
    provider_data: dict[str, Any]
    fetched_at: datetime

    def __post_init__(self) -> None:
        if isinstance(self.markers, list):
            object.__setattr__(self, "markers", tuple(self.markers))


@dataclass(frozen=True)
class AddMarkers:
    markers: tuple[Marker, ...]

    def __post_init__(self) -> None:
        if isinstance(self.markers, list):
            object.__setattr__(self, "markers", tuple(self.markers))


@dataclass(frozen=True)
class SetField:
    identifier: str
    value: Any


@dataclass(frozen=True)
class AppendNote:
    target: str
    text: str


@dataclass(frozen=True)
class ReplaceNote:
    target: str
    text: str


ChangeOp = AddMarkers | SetField | AppendNote | ReplaceNote


@dataclass(frozen=True)
class ChangeSet:
    clip_key: ClipKey
    ops: tuple[ChangeOp, ...]
    expected_etag: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.ops, list):
            object.__setattr__(self, "ops", tuple(self.ops))


@dataclass(frozen=True)
class ConflictDetail:
    kind: Literal["modified", "deleted", "marker-overlap"]
    expected_etag: str | None = None
    actual_etag: str | None = None
    fields: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class WriteResult:
    status: Literal["ok", "conflict", "retryable", "fatal"]
    upstream_response: dict[str, Any]
    new_etag: str | None = None
    conflict_detail: ConflictDetail | None = None


@dataclass(frozen=True)
class ClipQuery:
    text: str | None = None
    offset: int = 0
    limit: int = 50


@dataclass(frozen=True)
class ClipPage:
    items: tuple[CanonicalClip, ...]
    total: int
    offset: int
    limit: int

    def __post_init__(self) -> None:
        if isinstance(self.items, list):
            object.__setattr__(self, "items", tuple(self.items))


@dataclass(frozen=True)
class FieldDef:
    identifier: str
    name: str
    type: Literal[
        "text",
        "integer",
        "decimal",
        "date",
        "picklist",
        "multi-picklist",
        "bool",
    ]
    is_multi: bool
    is_editable: bool
    picklist_values: tuple[str, ...] | None = None
    provider_data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.picklist_values, list):
            object.__setattr__(self, "picklist_values", tuple(self.picklist_values))
