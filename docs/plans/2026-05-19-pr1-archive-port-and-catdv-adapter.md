# PR 1: Canonical Model + ArchiveProvider Port (CatDV-only) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce the `ArchiveProvider` port + a canonical domain model, route every CatDV access through a `CatdvArchiveAdapter`, and move `payload_builder` inside that adapter — without changing any user-visible behavior. This is the foundation for PRs 2–7 from the design spec at `docs/specs/2026-05-19-archive-abstraction-and-offline-mode-design.md`.

**Architecture:** New `backend/app/archive/` package contains canonical types (`CanonicalClip`, `Marker`, `ChangeSet`, `ChangeOp` variants) and an `ArchiveProvider` `Protocol`. A `CatdvArchiveAdapter` wraps the existing `CatdvClient` and is the only thing in the app that touches CatDV-shaped JSON. `AppContext` exposes `ctx.archive`; routes and the annotator stop calling `ctx.catdv` directly. `payload_builder.py` moves to `archive/providers/catdv/payload.py` and is refactored to consume `ChangeOp`s instead of `ReviewItem`s. Apply is still synchronous against live CatDV; the queue arrives in PR 4.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, dataclasses (frozen), `asyncio`, `pytest` + `pytest-asyncio`, `httpx`. No new third-party deps.

**Scope guardrail:** This plan implements ONLY what spec §13 PR 1 lists. No `AIInputStore`, no `clip_cache` table, no `pending_operations`, no workspaces, no FS adapter. Those are PRs 2–7 and get their own plans after this one ships.

---

## File map

### New files

| Path | Responsibility |
|---|---|
| `backend/app/archive/__init__.py` | Package marker. Re-exports `ArchiveProvider`, `CanonicalClip`, `ChangeSet`. |
| `backend/app/archive/model.py` | Canonical domain types: `ClipKey`, `Timecode`, `Marker`, `FieldValue`, `MediaRef`, `CanonicalClip`, `ChangeOp` variants, `ChangeSet`, `WriteResult`, `ClipPage`, `ClipQuery`. All frozen dataclasses. |
| `backend/app/archive/provider.py` | `ArchiveProvider` Protocol + `ProviderCapabilities`. |
| `backend/app/archive/errors.py` | Exception hierarchy: `ProviderError`, `AuthError`, `RetryableError`, `ConflictError`, `FatalProviderError`. |
| `backend/app/archive/registry.py` | `build_archive_provider(settings, *, catdv_client) -> ArchiveProvider` factory. Selects by `settings.archive_provider`. |
| `backend/app/archive/providers/__init__.py` | Package marker. |
| `backend/app/archive/providers/catdv/__init__.py` | Package marker. Re-exports `CatdvArchiveAdapter`. |
| `backend/app/archive/providers/catdv/adapter.py` | `CatdvArchiveAdapter` — implements `ArchiveProvider` against a `CatdvClient`. |
| `backend/app/archive/providers/catdv/mapping.py` | `from_catdv_clip(raw: dict) -> CanonicalClip`, `marker_to_catdv(marker, fps) -> dict`. |
| `backend/app/archive/providers/catdv/payload.py` | Moved from `backend/app/services/payload_builder.py`. Refactored to take `list[ChangeOp]` (no `ReviewItem`, no `TargetMap`). |
| `tests/unit/test_archive_model.py` | Constructs canonical types, asserts immutability and equality. |
| `tests/unit/test_catdv_mapping.py` | Round-trip a recorded clip JSON → `CanonicalClip` → opaque `provider_data` retained verbatim. |
| `tests/unit/test_catdv_payload.py` | Replaces `test_payload_builder.py` (moved). Tests the new `ChangeOp`-based signature. |
| `tests/unit/test_archive_registry.py` | Registry returns the right adapter, raises on unknown provider. |
| `tests/integration/test_catdv_adapter.py` | `CatdvArchiveAdapter` end-to-end against `FakeCatdv`: list, get, apply (markers + fields + notes). |
| `tests/fixtures/catdv_clip_sample.json` | A recorded full-shape CatDV clip JSON, scrubbed of real metadata. Used by mapping and adapter tests. |

### Modified files

| Path | Change |
|---|---|
| `backend/app/settings.py` | Add `archive_provider: Literal["catdv"] = "catdv"` (single value for now). |
| `backend/app/context.py` | Add `archive: ArchiveProvider \| None = None` field; build it in `build()` after `CatdvClient` is initialized; close it in `aclose()`. |
| `backend/app/routes/catdv.py` | Replace `ctx.catdv.list_clips(...)` and `ctx.catdv.get_clip(...)` with `ctx.archive.list_clips(...)` and `ctx.archive.get_clip(...)`. Keep route response shape (return `.provider_data` for `get_clip`). |
| `backend/app/routes/review.py` | Stop importing `build_put_payload`. Convert accepted `ReviewItem`s into `ChangeOp`s, build a `ChangeSet`, call `ctx.archive.apply_changes(...)`. Use returned `WriteResult` for `write_log`. |
| `backend/app/services/annotator.py` | Replace `catdv.get_clip` with `archive.get_clip`. Pull `clip_snapshot` from the returned `CanonicalClip.provider_data` (so the snapshot stays raw CatDV JSON, as today). Update function signature: drop `catdv` param, add `archive` param. |
| `backend/app/routes/jobs.py` | Pass `ctx.archive` instead of `ctx.catdv` to `run_job(...)`. Update the readiness gate to check `ctx.archive` instead of `ctx.catdv`. |
| `tests/integration/test_annotator_worker.py` | Existing `FakeCatdv` test helper renamed/repurposed to `FakeArchive` and exposes `get_clip()` returning a `CanonicalClip`. |
| `tests/integration/test_routes_catdv.py` | If asserting on response shape, no change needed; if it stubbed `ctx.catdv`, switch to stubbing `ctx.archive`. |
| `tests/integration/test_routes_review.py` | If it stubbed `ctx.catdv`, switch to stubbing `ctx.archive`. Assertions on `write_log` payload unchanged. |

### Deleted files

| Path | Reason |
|---|---|
| `backend/app/services/payload_builder.py` | Moved to `backend/app/archive/providers/catdv/payload.py` with a new signature. After all imports updated. |
| `tests/unit/test_payload_builder.py` | Replaced by `tests/unit/test_catdv_payload.py`. |

---

## Tasks

### Task 1: Canonical domain model

**Files:**
- Create: `backend/app/archive/__init__.py`
- Create: `backend/app/archive/model.py`
- Test: `tests/unit/test_archive_model.py`

- [x] **Step 1: Write the failing test**

Create `tests/unit/test_archive_model.py`:

```python
from datetime import datetime, timezone

import pytest

from backend.app.archive.model import (
    AddMarkers,
    AppendNote,
    CanonicalClip,
    ChangeSet,
    ClipKey,
    FieldValue,
    Marker,
    MediaRef,
    ReplaceNote,
    SetField,
    Timecode,
)


def test_clip_key_is_tuple_like():
    key: ClipKey = ("catdv", "12345")
    assert key[0] == "catdv"
    assert key[1] == "12345"


def test_timecode_holds_secs_and_fps():
    tc = Timecode(secs=4.0, fps=25.0)
    assert tc.secs == 4.0
    assert tc.fps == 25.0
    assert tc.frm is None
    assert tc.txt is None


def test_marker_requires_in_allows_optional_out():
    m = Marker(name="scene", in_=Timecode(secs=0.0, fps=25.0), out=None)
    assert m.out is None
    assert m.description is None


def test_canonical_clip_is_frozen():
    clip = CanonicalClip(
        key=("catdv", "1"),
        name="x",
        duration_secs=10.0,
        fps=25.0,
        markers=[],
        fields={},
        notes={},
        media=MediaRef(
            mime_type="video/quicktime",
            size_bytes=None,
            cached_path=None,
            upstream_handle="1",
        ),
        provider_data={"ID": 1},
        fetched_at=datetime.now(timezone.utc),
    )
    with pytest.raises(Exception):
        clip.name = "y"  # type: ignore[misc]


def test_change_ops_are_distinct_dataclasses():
    add = AddMarkers(markers=[Marker(name="s", in_=Timecode(secs=0.0, fps=25.0), out=None)])
    sf = SetField(identifier="pragafilm.dekáda.natočení", value="30.léta")
    an = AppendNote(target="notes", text="extra")
    rn = ReplaceNote(target="bigNotes", text="replaced")
    assert add != sf
    assert isinstance(add, AddMarkers)
    assert isinstance(sf, SetField)
    assert isinstance(an, AppendNote)
    assert isinstance(rn, ReplaceNote)


def test_change_set_groups_ops_for_one_clip():
    cs = ChangeSet(
        clip_key=("catdv", "1"),
        ops=[SetField(identifier="a", value=1), SetField(identifier="b", value=2)],
        expected_etag=None,
    )
    assert len(cs.ops) == 2
    assert cs.clip_key == ("catdv", "1")


def test_field_value_defaults_to_single_value():
    fv = FieldValue(identifier="x", value=1)
    assert fv.is_multi is False
```

- [x] **Step 2: Run test to verify it fails**

Run:
```bash
.venv/bin/pytest tests/unit/test_archive_model.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.app.archive'`.

- [x] **Step 3: Create empty package**

Create `backend/app/archive/__init__.py` with one line:

```python
"""Archive abstraction: providers, canonical model, write path."""
```

- [x] **Step 4: Implement `model.py`**

Create `backend/app/archive/model.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Union

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
        # Normalize markers to a tuple so the frozen dataclass stays hashable-shaped.
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


ChangeOp = Union[AddMarkers, SetField, AppendNote, ReplaceNote]


@dataclass(frozen=True)
class ChangeSet:
    clip_key: ClipKey
    ops: tuple[ChangeOp, ...]
    expected_etag: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.ops, list):
            object.__setattr__(self, "ops", tuple(self.ops))


@dataclass(frozen=True)
class WriteResult:
    status: Literal["ok", "conflict", "retryable", "fatal"]
    upstream_response: dict[str, Any]
    new_etag: str | None = None
    detail: str | None = None


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
```

- [x] **Step 5: Update `__init__.py` to re-export**

Replace `backend/app/archive/__init__.py` contents:

```python
"""Archive abstraction: providers, canonical model, write path."""

from backend.app.archive.model import (
    AddMarkers,
    AppendNote,
    CanonicalClip,
    ChangeOp,
    ChangeSet,
    ClipKey,
    ClipPage,
    ClipQuery,
    FieldValue,
    Marker,
    MediaRef,
    ProviderClipId,
    ProviderId,
    ReplaceNote,
    SetField,
    Timecode,
    WriteResult,
)

__all__ = [
    "AddMarkers",
    "AppendNote",
    "CanonicalClip",
    "ChangeOp",
    "ChangeSet",
    "ClipKey",
    "ClipPage",
    "ClipQuery",
    "FieldValue",
    "Marker",
    "MediaRef",
    "ProviderClipId",
    "ProviderId",
    "ReplaceNote",
    "SetField",
    "Timecode",
    "WriteResult",
]
```

- [x] **Step 6: Run tests, verify pass**

Run:
```bash
.venv/bin/pytest tests/unit/test_archive_model.py -v
```
Expected: 7 passed.

- [x] **Step 7: Commit**

```bash
git add backend/app/archive/__init__.py backend/app/archive/model.py tests/unit/test_archive_model.py
git commit -m "feat(archive): canonical domain model (Clip, Marker, ChangeSet)"
```

---

### Task 2: Errors and Protocol

**Files:**
- Create: `backend/app/archive/errors.py`
- Create: `backend/app/archive/provider.py`
- Test: `tests/unit/test_archive_protocol.py`

- [x] **Step 1: Write the failing test**

Create `tests/unit/test_archive_protocol.py`:

```python
from pathlib import Path

import pytest

from backend.app.archive.errors import (
    AuthError,
    ConflictError,
    FatalProviderError,
    ProviderError,
    RetryableError,
)
from backend.app.archive.provider import ArchiveProvider, ProviderCapabilities


def test_error_hierarchy():
    assert issubclass(AuthError, ProviderError)
    assert issubclass(RetryableError, ProviderError)
    assert issubclass(ConflictError, ProviderError)
    assert issubclass(FatalProviderError, ProviderError)


def test_capabilities_is_a_frozen_dataclass():
    caps = ProviderCapabilities(
        supports_markers=True,
        supports_notes=frozenset({"notes", "bigNotes"}),
        supports_field_create=False,
        supports_etag=False,
        media_is_local=False,
        write_atomicity="per-clip",
    )
    assert "notes" in caps.supports_notes
    with pytest.raises(Exception):
        caps.supports_markers = False  # type: ignore[misc]


def test_archive_provider_is_a_protocol():
    # Just check the names exist on the Protocol so adapters know what to implement.
    expected = {
        "id",
        "capabilities",
        "list_clips",
        "get_clip",
        "apply_changes",
    }
    assert expected.issubset(set(dir(ArchiveProvider)))
```

- [x] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/unit/test_archive_protocol.py -v
```
Expected: FAIL — `ModuleNotFoundError`.

- [x] **Step 3: Implement `errors.py`**

Create `backend/app/archive/errors.py`:

```python
class ProviderError(Exception):
    """Base for any error raised by an ArchiveProvider adapter."""


class AuthError(ProviderError):
    """Credentials rejected or session expired and cannot be re-established."""


class RetryableError(ProviderError):
    """Transient failure; caller may retry with backoff."""


class ConflictError(ProviderError):
    """Optimistic-concurrency conflict against upstream state."""


class FatalProviderError(ProviderError):
    """Non-retryable failure that requires operator attention."""
```

- [x] **Step 4: Implement `provider.py`**

Create `backend/app/archive/provider.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from backend.app.archive.model import (
    CanonicalClip,
    ChangeSet,
    ClipPage,
    ClipQuery,
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
    id: ProviderId
    capabilities: ProviderCapabilities

    async def list_clips(self, catalog: str, query: ClipQuery) -> ClipPage: ...
    async def get_clip(self, clip: ProviderClipId) -> CanonicalClip: ...
    async def apply_changes(self, change_set: ChangeSet) -> WriteResult: ...
```

- [x] **Step 5: Run tests, verify pass**

```bash
.venv/bin/pytest tests/unit/test_archive_protocol.py -v
```
Expected: 3 passed.

- [x] **Step 6: Commit**

```bash
git add backend/app/archive/errors.py backend/app/archive/provider.py tests/unit/test_archive_protocol.py
git commit -m "feat(archive): ArchiveProvider Protocol + error hierarchy"
```

---

### Task 3: Record a real-shape CatDV clip fixture

**Files:**
- Create: `tests/fixtures/catdv_clip_sample.json`

This fixture is small enough to embed inline — it captures the shape that `from_catdv_clip` must round-trip.

- [x] **Step 1: Create the fixture**

Create `tests/fixtures/catdv_clip_sample.json`:

```json
{
  "ID": 12345,
  "name": "Abramcukova_Anna_09",
  "notes": "Czech home movie, 9.5mm",
  "bigNotes": "Longer description here.",
  "format": "video/quicktime",
  "fps": 25.0,
  "in":       {"frm": 0,    "fmt": 25.0, "secs": 0.0,  "txt": "0:00:00:00"},
  "out":      {"frm": 8250, "fmt": 25.0, "secs": 330.0,"txt": "0:05:30:00"},
  "duration": {"frm": 8250, "fmt": 25.0, "secs": 330.0,"txt": "0:05:30:00"},
  "markers": [
    {
      "name": "Anna na zahradě",
      "category": "Event",
      "in":  {"frm": 1500, "fmt": 25.0, "secs": 60.0, "txt": "0:01:00:00"},
      "out": {"frm": 1750, "fmt": 25.0, "secs": 70.0, "txt": "0:01:10:00"},
      "description": "rodinný portrét",
      "color": "white"
    }
  ],
  "thumbnailIDs": [9001, 9002, 9003],
  "posterID": 9000,
  "media": {"sourceMediaID": 555},
  "importSource": {"path": "/Volumes/ARECA/ARCHIV/Abramcukova_Anna_09.mov"},
  "history": [],
  "fields": {
    "pragafilm.dekáda.natočení": "30.léta",
    "pragafilm.rok.natočení": ["1932", "1933"],
    "pragafilm.barva": "false",
    "pragafilm.popis.materialu": "rodinné záběry"
  },
  "modifyDate": "2026-05-18T10:00:00Z"
}
```

- [x] **Step 2: Commit**

```bash
git add tests/fixtures/catdv_clip_sample.json
git commit -m "test(archive): record CatDV clip sample fixture"
```

---

### Task 4: CatDV ↔ canonical mapping

**Files:**
- Create: `backend/app/archive/providers/__init__.py`
- Create: `backend/app/archive/providers/catdv/__init__.py`
- Create: `backend/app/archive/providers/catdv/mapping.py`
- Test: `tests/unit/test_catdv_mapping.py`

- [x] **Step 1: Write the failing test**

Create `tests/unit/test_catdv_mapping.py`:

```python
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.app.archive.model import Marker, Timecode
from backend.app.archive.providers.catdv.mapping import (
    from_catdv_clip,
    marker_to_catdv,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "catdv_clip_sample.json"


@pytest.fixture
def raw() -> dict:
    return json.loads(FIXTURE.read_text())


def test_from_catdv_clip_sets_key(raw):
    clip = from_catdv_clip(raw, fetched_at=datetime.now(timezone.utc))
    assert clip.key == ("catdv", "12345")
    assert clip.name == "Abramcukova_Anna_09"


def test_from_catdv_clip_extracts_fps_and_duration(raw):
    clip = from_catdv_clip(raw, fetched_at=datetime.now(timezone.utc))
    assert clip.fps == 25.0
    assert clip.duration_secs == 330.0


def test_from_catdv_clip_extracts_markers(raw):
    clip = from_catdv_clip(raw, fetched_at=datetime.now(timezone.utc))
    assert len(clip.markers) == 1
    m = clip.markers[0]
    assert m.name == "Anna na zahradě"
    assert m.in_.secs == 60.0
    assert m.in_.fps == 25.0
    assert m.in_.frm == 1500
    assert m.out is not None and m.out.secs == 70.0


def test_from_catdv_clip_preserves_provider_data_verbatim(raw):
    clip = from_catdv_clip(raw, fetched_at=datetime.now(timezone.utc))
    assert clip.provider_data == raw  # exact round-trip pointer


def test_from_catdv_clip_extracts_notes(raw):
    clip = from_catdv_clip(raw, fetched_at=datetime.now(timezone.utc))
    assert clip.notes["notes"] == "Czech home movie, 9.5mm"
    assert clip.notes["bigNotes"] == "Longer description here."


def test_from_catdv_clip_extracts_pragafilm_fields(raw):
    clip = from_catdv_clip(raw, fetched_at=datetime.now(timezone.utc))
    assert "pragafilm.dekáda.natočení" in clip.fields
    fv = clip.fields["pragafilm.dekáda.natočení"]
    assert fv.value == "30.léta"
    assert fv.is_multi is False
    fv_years = clip.fields["pragafilm.rok.natočení"]
    assert fv_years.value == ["1932", "1933"]
    assert fv_years.is_multi is True


def test_marker_to_catdv_expands_partial_timecode():
    m = Marker(
        name="scene-1",
        in_=Timecode(secs=4.0, fps=25.0),
        out=Timecode(secs=6.0, fps=25.0),
    )
    raw = marker_to_catdv(m, fps=25.0)
    assert raw["name"] == "scene-1"
    assert raw["in"]["secs"] == 4.0
    assert raw["in"]["frm"] == 100
    assert raw["in"]["fmt"] == 25.0
    assert raw["in"]["txt"] == "0:00:04:00"
    assert raw["out"]["frm"] == 150
```

- [x] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/unit/test_catdv_mapping.py -v
```
Expected: FAIL — `ModuleNotFoundError`.

- [x] **Step 3: Create empty provider packages**

Create `backend/app/archive/providers/__init__.py`:

```python
"""Archive provider implementations."""
```

Create `backend/app/archive/providers/catdv/__init__.py`:

```python
"""CatDV archive provider."""
```

- [x] **Step 4: Implement `mapping.py`**

Create `backend/app/archive/providers/catdv/mapping.py`:

```python
from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.app.archive.model import (
    CanonicalClip,
    FieldValue,
    Marker,
    MediaRef,
    Timecode,
)
from backend.app.timecode import secs_to_smpte

DEFAULT_FPS = 25.0


def from_catdv_clip(raw: dict[str, Any], *, fetched_at: datetime) -> CanonicalClip:
    clip_id = str(raw["ID"])
    fps = float(raw.get("fps") or DEFAULT_FPS)

    duration_secs = 0.0
    dur = raw.get("duration")
    if isinstance(dur, dict) and isinstance(dur.get("secs"), (int, float)):
        duration_secs = float(dur["secs"])

    markers = tuple(_marker_from_catdv(m, fps) for m in raw.get("markers", []) or [])
    fields = {
        identifier: FieldValue(
            identifier=identifier,
            value=value,
            is_multi=isinstance(value, list),
        )
        for identifier, value in (raw.get("fields") or {}).items()
    }
    notes: dict[str, str] = {}
    for key in ("notes", "bigNotes"):
        v = raw.get(key)
        if isinstance(v, str):
            notes[key] = v

    media = MediaRef(
        mime_type=raw.get("format") or "video/quicktime",
        size_bytes=None,
        cached_path=None,
        upstream_handle=clip_id,
    )

    return CanonicalClip(
        key=("catdv", clip_id),
        name=str(raw.get("name", "")),
        duration_secs=duration_secs,
        fps=fps,
        markers=markers,
        fields=fields,
        notes=notes,
        media=media,
        provider_data=raw,
        fetched_at=fetched_at,
    )


def _marker_from_catdv(raw: dict[str, Any], fps: float) -> Marker:
    return Marker(
        name=str(raw.get("name", "")),
        in_=_timecode_from_catdv(raw.get("in") or {}, fps),
        out=_timecode_from_catdv(raw["out"], fps) if isinstance(raw.get("out"), dict) else None,
        description=raw.get("description"),
        category=raw.get("category"),
        color=raw.get("color"),
    )


def _timecode_from_catdv(raw: dict[str, Any], default_fps: float) -> Timecode:
    fps_v = raw.get("fmt")
    fps = float(fps_v) if isinstance(fps_v, (int, float)) and fps_v > 0 else default_fps
    secs_v = raw.get("secs")
    secs = float(secs_v) if isinstance(secs_v, (int, float)) else 0.0
    frm_v = raw.get("frm")
    frm = int(frm_v) if isinstance(frm_v, int) else None
    txt = raw.get("txt") if isinstance(raw.get("txt"), str) else None
    return Timecode(secs=secs, fps=fps, frm=frm, txt=txt)


def marker_to_catdv(marker: Marker, fps: float) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": marker.name,
        "in": _timecode_to_catdv(marker.in_, fps),
    }
    if marker.out is not None:
        out["out"] = _timecode_to_catdv(marker.out, fps)
    if marker.description is not None:
        out["description"] = marker.description
    if marker.category is not None:
        out["category"] = marker.category
    if marker.color is not None:
        out["color"] = marker.color
    return out


def _timecode_to_catdv(tc: Timecode, default_fps: float) -> dict[str, Any]:
    fps = tc.fps if tc.fps > 0 else default_fps
    secs = float(tc.secs)
    frm = tc.frm if tc.frm is not None else round(secs * fps)
    txt = tc.txt if tc.txt is not None else secs_to_smpte(secs, fps)
    return {"frm": frm, "fmt": float(fps), "secs": secs, "txt": txt}
```

- [x] **Step 5: Run tests, verify pass**

```bash
.venv/bin/pytest tests/unit/test_catdv_mapping.py -v
```
Expected: 7 passed.

- [x] **Step 6: Commit**

```bash
git add backend/app/archive/providers tests/unit/test_catdv_mapping.py
git commit -m "feat(archive/catdv): CanonicalClip <-> CatDV JSON mapping"
```

---

### Task 5: Move payload_builder into adapter (refactor to ChangeOps)

**Files:**
- Create: `backend/app/archive/providers/catdv/payload.py`
- Create: `tests/unit/test_catdv_payload.py`
- (Will delete `backend/app/services/payload_builder.py` and `tests/unit/test_payload_builder.py` in Task 11 once callers migrate.)

- [x] **Step 1: Write the failing test**

Create `tests/unit/test_catdv_payload.py`:

```python
import pytest

from backend.app.archive.model import (
    AddMarkers,
    AppendNote,
    Marker,
    ReplaceNote,
    SetField,
    Timecode,
)
from backend.app.archive.providers.catdv.payload import build_put_payload


def _clip(markers=None, fields=None, notes=None, big_notes=None, fps=25.0):
    out = {"ID": 1, "name": "c", "fps": fps, "markers": markers or [], "fields": fields or {}}
    if notes is not None:
        out["notes"] = notes
    if big_notes is not None:
        out["bigNotes"] = big_notes
    return out


def test_no_ops_returns_empty_payload():
    assert build_put_payload(current=_clip(), ops=[]) == {}


def test_add_markers_appends_to_existing_and_normalizes_timecode():
    existing = [
        {
            "name": "m0",
            "in": {"frm": 0, "fmt": 25.0, "secs": 0.0, "txt": "0:00:00:00"},
            "out": {"frm": 25, "fmt": 25.0, "secs": 1.0, "txt": "0:00:01:00"},
        }
    ]
    op = AddMarkers(
        markers=[
            Marker(
                name="m1",
                in_=Timecode(secs=4.0, fps=25.0),
                out=Timecode(secs=6.0, fps=25.0),
            )
        ]
    )
    payload = build_put_payload(current=_clip(markers=existing), ops=[op])
    assert "markers" in payload
    assert len(payload["markers"]) == 2
    new_m = payload["markers"][1]
    assert new_m["in"]["frm"] == 100
    assert new_m["in"]["fmt"] == 25.0
    assert new_m["in"]["txt"] == "0:00:04:00"


def test_add_markers_dedupes_on_existing_in_frm():
    existing = [
        {
            "name": "m0",
            "in": {"frm": 100, "fmt": 25.0, "secs": 4.0, "txt": "0:00:04:00"},
            "out": {"frm": 150, "fmt": 25.0, "secs": 6.0, "txt": "0:00:06:00"},
        }
    ]
    op = AddMarkers(
        markers=[Marker(name="dup", in_=Timecode(secs=4.0, fps=25.0), out=None)]
    )
    payload = build_put_payload(current=_clip(markers=existing), ops=[op])
    assert len(payload["markers"]) == 1


def test_set_field_writes_to_fields_map():
    op = SetField(identifier="pragafilm.dekáda.natočení", value="30.léta")
    payload = build_put_payload(current=_clip(), ops=[op])
    assert payload == {"fields": {"pragafilm.dekáda.natočení": "30.léta"}}


def test_append_note_joins_with_separator_when_existing_present():
    op = AppendNote(target="notes", text="new line")
    payload = build_put_payload(current=_clip(notes="old"), ops=[op])
    assert payload["fields"]["notes"] == "old\n\n---\n\nnew line"


def test_append_note_writes_directly_when_no_existing():
    op = AppendNote(target="notes", text="new")
    payload = build_put_payload(current=_clip(), ops=[op])
    assert payload["fields"]["notes"] == "new"


def test_replace_note_overrides_existing():
    op = ReplaceNote(target="bigNotes", text="fresh")
    payload = build_put_payload(current=_clip(big_notes="old"), ops=[op])
    assert payload["fields"]["bigNotes"] == "fresh"


def test_multiple_ops_combined_in_one_payload():
    op_m = AddMarkers(
        markers=[Marker(name="m", in_=Timecode(secs=2.0, fps=25.0), out=None)]
    )
    op_f = SetField(identifier="pragafilm.barva", value="true")
    op_n = AppendNote(target="notes", text="x")
    payload = build_put_payload(current=_clip(), ops=[op_m, op_f, op_n])
    assert "markers" in payload
    assert payload["fields"]["pragafilm.barva"] == "true"
    assert payload["fields"]["notes"] == "x"
```

- [x] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/unit/test_catdv_payload.py -v
```
Expected: FAIL — `ModuleNotFoundError`.

- [x] **Step 3: Implement `payload.py`**

Create `backend/app/archive/providers/catdv/payload.py`:

```python
from __future__ import annotations

from typing import Any, Iterable

from backend.app.archive.model import (
    AddMarkers,
    AppendNote,
    ChangeOp,
    ReplaceNote,
    SetField,
)
from backend.app.archive.providers.catdv.mapping import marker_to_catdv

NOTE_SEPARATOR = "\n\n---\n\n"
DEFAULT_FPS = 25.0


def build_put_payload(
    *,
    current: dict[str, Any],
    ops: Iterable[ChangeOp],
) -> dict[str, Any]:
    """Build a minimal CatDV PUT body from a list of ChangeOps.

    Invariants:
      - markers array is replaced wholesale by CatDV PUT, so any AddMarkers op
        must merge with existing markers and dedupe on in.frm.
      - other arrays/fields not touched by ops do NOT appear in the payload.
      - AppendNote joins with `\n\n---\n\n` separator when prior text exists.
    """
    payload: dict[str, Any] = {}
    fps = _clip_fps(current)

    ops_list = list(ops)
    marker_ops = [o for o in ops_list if isinstance(o, AddMarkers)]
    if marker_ops:
        existing = list(current.get("markers") or [])
        existing_frms = {_in_frm(m) for m in existing if _in_frm(m) is not None}
        new_markers: list[dict[str, Any]] = []
        for op in marker_ops:
            for marker in op.markers:
                raw = marker_to_catdv(marker, fps)
                frm = _in_frm(raw)
                if frm is not None and frm in existing_frms:
                    continue
                new_markers.append(raw)
                if frm is not None:
                    existing_frms.add(frm)
        payload["markers"] = existing + new_markers

    field_changes: dict[str, Any] = {}
    for op in ops_list:
        if isinstance(op, SetField):
            field_changes[op.identifier] = op.value
        elif isinstance(op, AppendNote):
            existing_text = _existing_text(current, op.target) or ""
            if existing_text:
                field_changes[op.target] = existing_text + NOTE_SEPARATOR + op.text
            else:
                field_changes[op.target] = op.text
        elif isinstance(op, ReplaceNote):
            field_changes[op.target] = op.text

    if field_changes:
        payload["fields"] = field_changes
    return payload


def _clip_fps(current: dict[str, Any]) -> float:
    fps = current.get("fps")
    if isinstance(fps, (int, float)) and fps > 0:
        return float(fps)
    for m in current.get("markers") or []:
        in_obj = m.get("in") if isinstance(m, dict) else None
        if isinstance(in_obj, dict):
            f = in_obj.get("fmt")
            if isinstance(f, (int, float)) and f > 0:
                return float(f)
    return DEFAULT_FPS


def _in_frm(marker: dict[str, Any]) -> int | None:
    in_obj = marker.get("in") if isinstance(marker, dict) else None
    if isinstance(in_obj, dict):
        v = in_obj.get("frm")
        if isinstance(v, int):
            return v
    return None


def _existing_text(current: dict[str, Any], identifier: str) -> str | None:
    if identifier in ("notes", "bigNotes"):
        v = current.get(identifier)
        return v if isinstance(v, str) else None
    fields = current.get("fields") or {}
    v = fields.get(identifier)
    return v if isinstance(v, str) else None
```

- [x] **Step 4: Run tests, verify pass**

```bash
.venv/bin/pytest tests/unit/test_catdv_payload.py -v
```
Expected: 8 passed.

- [x] **Step 5: Commit**

```bash
git add backend/app/archive/providers/catdv/payload.py tests/unit/test_catdv_payload.py
git commit -m "feat(archive/catdv): build_put_payload from ChangeOps"
```

---

### Task 6: CatdvArchiveAdapter — list_clips + get_clip

**Files:**
- Create: `backend/app/archive/providers/catdv/adapter.py`
- Test: `tests/integration/test_catdv_adapter.py`

- [x] **Step 1: Write the failing test**

Create `tests/integration/test_catdv_adapter.py`:

```python
from datetime import datetime, timezone

import pytest

from backend.app.archive.model import (
    AddMarkers,
    ChangeSet,
    ClipQuery,
    Marker,
    SetField,
    Timecode,
)
from backend.app.archive.providers.catdv.adapter import CatdvArchiveAdapter
from backend.app.services.catdv_client import CatdvClient
from tests.fakes.fake_catdv import running_fake_catdv


@pytest.mark.asyncio
async def test_adapter_list_clips_returns_clip_page():
    with running_fake_catdv() as (base_url, fake):
        fake.clips[1] = {"ID": 1, "name": "Clip_1", "markers": [], "fps": 25.0}
        fake.clips[2] = {"ID": 2, "name": "Clip_2", "markers": [], "fps": 25.0}
        async with CatdvClient(base_url, "klientAI", "secret") as client:
            adapter = CatdvArchiveAdapter(client=client)
            page = await adapter.list_clips("881507", ClipQuery(limit=10))
        assert page.total == 2
        assert {c.name for c in page.items} == {"Clip_1", "Clip_2"}


@pytest.mark.asyncio
async def test_adapter_get_clip_returns_canonical_clip_with_provider_data():
    with running_fake_catdv() as (base_url, fake):
        fake.clips[42] = {
            "ID": 42,
            "name": "Clip_42",
            "fps": 25.0,
            "markers": [],
            "fields": {"pragafilm.barva": "true"},
        }
        async with CatdvClient(base_url, "klientAI", "secret") as client:
            adapter = CatdvArchiveAdapter(client=client)
            clip = await adapter.get_clip("42")
        assert clip.key == ("catdv", "42")
        assert clip.name == "Clip_42"
        assert clip.provider_data["ID"] == 42
        assert "pragafilm.barva" in clip.fields


@pytest.mark.asyncio
async def test_adapter_capabilities_reflect_catdv():
    with running_fake_catdv() as (base_url, _):
        async with CatdvClient(base_url, "klientAI", "secret") as client:
            adapter = CatdvArchiveAdapter(client=client)
        caps = adapter.capabilities
        assert caps.supports_markers is True
        assert caps.supports_etag is False
        assert caps.write_atomicity == "per-clip"
        assert "notes" in caps.supports_notes
        assert "bigNotes" in caps.supports_notes
```

- [x] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/integration/test_catdv_adapter.py -v
```
Expected: FAIL — `ModuleNotFoundError`.

- [x] **Step 3: Implement `adapter.py` (partial — list + get only)**

Create `backend/app/archive/providers/catdv/adapter.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

from backend.app.archive.errors import (
    AuthError,
    FatalProviderError,
    ProviderError,
    RetryableError,
)
from backend.app.archive.model import (
    CanonicalClip,
    ChangeSet,
    ClipPage,
    ClipQuery,
    WriteResult,
)
from backend.app.archive.provider import ProviderCapabilities
from backend.app.archive.providers.catdv.mapping import from_catdv_clip
from backend.app.services.catdv_client import (
    CatdvAuthError,
    CatdvBusyError,
    CatdvClient,
    CatdvError,
)


class CatdvArchiveAdapter:
    id = "catdv"
    capabilities = ProviderCapabilities(
        supports_markers=True,
        supports_notes=frozenset({"notes", "bigNotes"}),
        supports_field_create=False,
        supports_etag=False,
        media_is_local=False,
        write_atomicity="per-clip",
    )

    def __init__(self, *, client: CatdvClient) -> None:
        self._client = client

    async def list_clips(self, catalog: str, query: ClipQuery) -> ClipPage:
        try:
            data = await self._client.list_clips(
                int(catalog),
                offset=query.offset,
                limit=query.limit,
                q=query.text,
            )
        except CatdvAuthError as exc:
            raise AuthError(str(exc)) from exc
        except CatdvBusyError as exc:
            raise RetryableError(str(exc)) from exc
        except CatdvError as exc:
            raise FatalProviderError(str(exc)) from exc

        now = datetime.now(timezone.utc)
        raw_items = data.get("clips") if isinstance(data, dict) else []
        items = tuple(from_catdv_clip(raw, fetched_at=now) for raw in (raw_items or []))
        return ClipPage(
            items=items,
            total=int((data or {}).get("total", len(items))),
            offset=query.offset,
            limit=query.limit,
        )

    async def get_clip(self, clip: str) -> CanonicalClip:
        try:
            raw = await self._client.get_clip(int(clip))
        except CatdvAuthError as exc:
            raise AuthError(str(exc)) from exc
        except CatdvBusyError as exc:
            raise RetryableError(str(exc)) from exc
        except CatdvError as exc:
            raise FatalProviderError(str(exc)) from exc
        return from_catdv_clip(raw, fetched_at=datetime.now(timezone.utc))

    async def apply_changes(self, change_set: ChangeSet) -> WriteResult:
        raise NotImplementedError  # implemented in Task 7
```

- [x] **Step 4: Run tests, verify pass**

```bash
.venv/bin/pytest tests/integration/test_catdv_adapter.py -v
```
Expected: 3 passed.

- [x] **Step 5: Commit**

```bash
git add backend/app/archive/providers/catdv/adapter.py tests/integration/test_catdv_adapter.py
git commit -m "feat(archive/catdv): adapter list_clips + get_clip + capabilities"
```

---

### Task 7: CatdvArchiveAdapter — apply_changes

**Files:**
- Modify: `backend/app/archive/providers/catdv/adapter.py`
- Modify: `tests/integration/test_catdv_adapter.py`

- [x] **Step 1: Add failing tests**

Append to `tests/integration/test_catdv_adapter.py`:

```python
@pytest.mark.asyncio
async def test_apply_changes_adds_marker_via_put():
    with running_fake_catdv() as (base_url, fake):
        fake.clips[7] = {"ID": 7, "name": "c", "fps": 25.0, "markers": [], "fields": {}}
        async with CatdvClient(base_url, "klientAI", "secret") as client:
            adapter = CatdvArchiveAdapter(client=client)
            cs = ChangeSet(
                clip_key=("catdv", "7"),
                ops=[
                    AddMarkers(
                        markers=[
                            Marker(
                                name="m",
                                in_=Timecode(secs=4.0, fps=25.0),
                                out=Timecode(secs=6.0, fps=25.0),
                            )
                        ]
                    )
                ],
            )
            result = await adapter.apply_changes(cs)
        assert result.status == "ok"
        # Inspect the recorded PUT body
        assert len(fake.put_log) == 1
        clip_id, body = fake.put_log[0]
        assert clip_id == 7
        assert len(body["markers"]) == 1
        assert body["markers"][0]["in"]["frm"] == 100


@pytest.mark.asyncio
async def test_apply_changes_setfield_writes_minimal_payload():
    with running_fake_catdv() as (base_url, fake):
        fake.clips[8] = {"ID": 8, "name": "c", "fps": 25.0, "markers": [], "fields": {}}
        async with CatdvClient(base_url, "klientAI", "secret") as client:
            adapter = CatdvArchiveAdapter(client=client)
            cs = ChangeSet(
                clip_key=("catdv", "8"),
                ops=[SetField(identifier="pragafilm.barva", value="true")],
            )
            result = await adapter.apply_changes(cs)
        assert result.status == "ok"
        _, body = fake.put_log[0]
        assert body == {"fields": {"pragafilm.barva": "true"}}


@pytest.mark.asyncio
async def test_apply_changes_returns_fatal_on_catdv_error():
    with running_fake_catdv() as (base_url, fake):
        # Clip 99 does not exist → fake returns ERROR envelope.
        async with CatdvClient(base_url, "klientAI", "secret") as client:
            adapter = CatdvArchiveAdapter(client=client)
            cs = ChangeSet(
                clip_key=("catdv", "99"),
                ops=[SetField(identifier="x", value=1)],
            )
            with pytest.raises(FatalProviderError):
                await adapter.apply_changes(cs)
```

Add to the test file's imports:

```python
from backend.app.archive.errors import FatalProviderError
```

- [x] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/integration/test_catdv_adapter.py -v
```
Expected: 3 new tests FAIL with `NotImplementedError` (or `FatalProviderError` not raised).

- [x] **Step 3: Implement `apply_changes` in adapter**

In `backend/app/archive/providers/catdv/adapter.py`, replace the `apply_changes` stub with:

```python
    async def apply_changes(self, change_set: ChangeSet) -> WriteResult:
        provider_id, clip_id_str = change_set.clip_key
        if provider_id != self.id:
            raise FatalProviderError(
                f"ChangeSet for provider {provider_id!r} sent to catdv adapter"
            )
        from backend.app.archive.providers.catdv.payload import build_put_payload

        try:
            current = await self._client.get_clip(int(clip_id_str))
        except CatdvAuthError as exc:
            raise AuthError(str(exc)) from exc
        except CatdvBusyError as exc:
            raise RetryableError(str(exc)) from exc
        except CatdvError as exc:
            raise FatalProviderError(str(exc)) from exc

        payload = build_put_payload(current=current, ops=list(change_set.ops))
        if not payload:
            return WriteResult(status="ok", upstream_response={}, detail="no-op")

        try:
            response = await self._client.put_clip(int(clip_id_str), payload)
        except CatdvAuthError as exc:
            raise AuthError(str(exc)) from exc
        except CatdvBusyError as exc:
            raise RetryableError(str(exc)) from exc
        except CatdvError as exc:
            raise FatalProviderError(str(exc)) from exc

        return WriteResult(status="ok", upstream_response=response)
```

- [x] **Step 4: Run all adapter tests, verify pass**

```bash
.venv/bin/pytest tests/integration/test_catdv_adapter.py -v
```
Expected: 6 passed.

- [x] **Step 5: Commit**

```bash
git add backend/app/archive/providers/catdv/adapter.py tests/integration/test_catdv_adapter.py
git commit -m "feat(archive/catdv): adapter.apply_changes wraps get+merge+PUT"
```

---

### Task 8: Registry + settings knob

**Files:**
- Create: `backend/app/archive/registry.py`
- Modify: `backend/app/settings.py`
- Test: `tests/unit/test_archive_registry.py`

- [ ] **Step 1: Inspect current settings**

Read `backend/app/settings.py` (don't modify yet) — confirm it's a `pydantic-settings` `BaseSettings` subclass.

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_archive_registry.py`:

```python
import pytest

from backend.app.archive.providers.catdv.adapter import CatdvArchiveAdapter
from backend.app.archive.registry import build_archive_provider


class DummyClient:
    pass


def test_build_returns_catdv_adapter_when_settings_says_catdv():
    class S:
        archive_provider = "catdv"

    provider = build_archive_provider(S(), catdv_client=DummyClient())
    assert isinstance(provider, CatdvArchiveAdapter)


def test_build_raises_on_unknown_provider():
    class S:
        archive_provider = "wat"

    with pytest.raises(ValueError, match="unknown"):
        build_archive_provider(S(), catdv_client=DummyClient())


def test_build_raises_when_catdv_client_missing_for_catdv():
    class S:
        archive_provider = "catdv"

    with pytest.raises(ValueError, match="catdv_client"):
        build_archive_provider(S(), catdv_client=None)
```

- [ ] **Step 3: Run test to verify it fails**

```bash
.venv/bin/pytest tests/unit/test_archive_registry.py -v
```
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 4: Implement `registry.py`**

Create `backend/app/archive/registry.py`:

```python
from __future__ import annotations

from typing import Any

from backend.app.archive.provider import ArchiveProvider
from backend.app.archive.providers.catdv.adapter import CatdvArchiveAdapter


def build_archive_provider(settings: Any, *, catdv_client: Any) -> ArchiveProvider:
    """Construct the active ArchiveProvider from settings.

    `settings` is duck-typed (only `archive_provider` is read) so this is easy
    to test without the full pydantic-settings instance.
    """
    name = getattr(settings, "archive_provider", "catdv")
    if name == "catdv":
        if catdv_client is None:
            raise ValueError("archive_provider=catdv requires a catdv_client")
        return CatdvArchiveAdapter(client=catdv_client)
    raise ValueError(f"unknown archive_provider: {name!r}")
```

- [ ] **Step 5: Add `archive_provider` to Settings**

In `backend/app/settings.py`, add a field. Find the existing `class Settings(BaseSettings):` block and add:

```python
    archive_provider: str = "catdv"
```

(Use `str` rather than `Literal["catdv"]` so future PRs can add values without breaking validation right away.)

- [ ] **Step 6: Run all unit tests, verify pass**

```bash
.venv/bin/pytest tests/unit -v
```
Expected: all green, including the new 3 registry tests.

- [ ] **Step 7: Commit**

```bash
git add backend/app/archive/registry.py backend/app/settings.py tests/unit/test_archive_registry.py
git commit -m "feat(archive): registry + settings.archive_provider knob"
```

---

### Task 9: Wire `AppContext.archive`

**Files:**
- Modify: `backend/app/context.py`
- Test: extend `tests/integration/test_context.py` (or add new test)

- [ ] **Step 1: Add failing test**

Append to `tests/integration/test_context.py` (read it first to match existing style):

```python
@pytest.mark.asyncio
async def test_context_exposes_archive_provider_when_external_initialized(tmp_path, monkeypatch):
    # Use the same pattern existing tests use to build context with init_external=True.
    # The fake CatDV needs to be running for CatdvClient to construct cleanly; we
    # only assert ctx.archive is wired, not that it talks to a real CatDV here.
    from backend.app.context import AppContext
    from backend.app.settings import Settings
    from tests.fakes.fake_catdv import running_fake_catdv

    with running_fake_catdv() as (base_url, _):
        monkeypatch.setenv("CATDV_BASE_URL", base_url)
        monkeypatch.setenv("CATDV_USERNAME", "klientAI")
        monkeypatch.setenv("CATDV_PASSWORD", "secret")
        monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
        monkeypatch.setenv("GCP_PROJECT_ID", "p")
        monkeypatch.setenv("GCS_BUCKET_NAME", "b")
        monkeypatch.setenv("GCP_LOCATION", "europe-west3")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        s = Settings()
        ctx = await AppContext.build(s, init_external=True)
        try:
            from backend.app.archive.providers.catdv.adapter import CatdvArchiveAdapter

            assert isinstance(ctx.archive, CatdvArchiveAdapter)
        finally:
            await ctx.aclose()
```

(If the existing `test_context.py` doesn't import the things this test needs, add them. Don't break existing tests.)

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/integration/test_context.py -v
```
Expected: FAIL — `ctx.archive` is `None` or missing.

- [ ] **Step 3: Add `archive` field + wiring in `context.py`**

In `backend/app/context.py`:

1. Add import at top:
   ```python
   from backend.app.archive.provider import ArchiveProvider
   from backend.app.archive.registry import build_archive_provider
   ```
2. Add field to the dataclass (next to existing `catdv = None`):
   ```python
   archive: ArchiveProvider | None = None
   ```
3. Inside `build()`, after `ctx.catdv = CatdvClient(...)` and `await ctx.catdv.__aenter__()` lines, add:
   ```python
   ctx.archive = build_archive_provider(settings, catdv_client=ctx.catdv)
   ```

- [ ] **Step 4: Run tests, verify pass**

```bash
.venv/bin/pytest tests/integration/test_context.py -v
```
Expected: pass.

- [ ] **Step 5: Run full test suite to catch regressions**

```bash
.venv/bin/pytest -q
```
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add backend/app/context.py tests/integration/test_context.py
git commit -m "feat(context): build and expose ctx.archive at startup"
```

---

### Task 10: Switch routes/catdv.py to use `ctx.archive`

**Files:**
- Modify: `backend/app/routes/catdv.py`
- Verify: `tests/integration/test_routes_catdv.py` still passes (and adjust if it stubbed `ctx.catdv`).

- [ ] **Step 1: Read the existing route test**

```bash
.venv/bin/pytest tests/integration/test_routes_catdv.py -v
```

Run before changing anything; confirm baseline pass count.

- [ ] **Step 2: Modify `routes/catdv.py`**

Replace the file contents with:

```python
from fastapi import APIRouter, HTTPException, Request

from backend.app.archive.errors import ProviderError
from backend.app.archive.model import ClipQuery

router = APIRouter(prefix="/api/catdv", tags=["catdv"])


@router.get("/clips")
async def list_clips(request: Request, q: str | None = None, offset: int = 0, limit: int = 50):
    ctx = request.app.state.ctx
    if ctx.archive is None:
        raise HTTPException(503, "archive provider not initialized")
    try:
        page = await ctx.archive.list_clips(
            str(ctx.settings.catdv_catalog_id),
            ClipQuery(text=q, offset=offset, limit=limit),
        )
    except ProviderError as exc:
        raise HTTPException(502, f"archive error: {exc}")
    return {
        "total": page.total,
        "clips": [c.provider_data for c in page.items],
    }


@router.get("/clips/{clip_id}")
async def get_clip(request: Request, clip_id: int):
    ctx = request.app.state.ctx
    if ctx.archive is None:
        raise HTTPException(503, "archive provider not initialized")
    try:
        clip = await ctx.archive.get_clip(str(clip_id))
    except ProviderError as exc:
        raise HTTPException(502, f"archive error: {exc}")
    return clip.provider_data
```

- [ ] **Step 3: Run route tests, fix breakage if any**

```bash
.venv/bin/pytest tests/integration/test_routes_catdv.py -v
```

If a test was stubbing `ctx.catdv`, change it to stub `ctx.archive` with a small fake that implements `list_clips`/`get_clip`. The route-level response shape is unchanged.

- [ ] **Step 4: Run full suite**

```bash
.venv/bin/pytest -q
```
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routes/catdv.py tests/integration/test_routes_catdv.py
git commit -m "refactor(routes/catdv): consume ArchiveProvider via ctx.archive"
```

---

### Task 11: Switch routes/review.py to use `ctx.archive.apply_changes`

**Files:**
- Modify: `backend/app/routes/review.py`
- Verify: `tests/integration/test_routes_review.py` still passes.

- [ ] **Step 1: Read existing review route tests**

```bash
.venv/bin/pytest tests/integration/test_routes_review.py -v
```

Confirm baseline; note which assertions touch `write_log` payload shape (those must stay identical).

- [ ] **Step 2: Modify `routes/review.py`**

Replace `apply_clip` with a version that converts `ReviewItem`s to `ChangeOp`s and calls `archive.apply_changes`:

```python
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.app.archive.errors import ProviderError
from backend.app.archive.model import (
    AddMarkers,
    AppendNote,
    ChangeOp,
    ChangeSet,
    Marker,
    ReplaceNote,
    SetField,
    Timecode,
)
from backend.app.models.annotation import ReviewItem
from backend.app.models.template import TargetMap

router = APIRouter(prefix="/api/review", tags=["review"])


class Decision(BaseModel):
    decision: str
    edited_value: Any = None


@router.get("/clips/{clip_id}/items")
async def list_items_for_clip(request: Request, clip_id: int):
    ctx = request.app.state.ctx
    items = await ctx.review_items_repo.list_by_clip(ctx.db, clip_id)
    return [it.model_dump() for it in items]


@router.post("/items/{item_id}/decision")
async def set_decision(request: Request, item_id: int, body: Decision):
    ctx = request.app.state.ctx
    if body.decision not in ("accepted", "rejected", "pending"):
        raise HTTPException(400, "decision must be accepted|rejected|pending")
    await ctx.review_items_repo.set_decision(
        ctx.db,
        item_id,
        body.decision,
        edited_value=body.edited_value,
    )
    return {"id": item_id, "decision": body.decision}


@router.post("/clips/{clip_id}/apply")
async def apply_clip(request: Request, clip_id: int):
    ctx = request.app.state.ctx
    if ctx.archive is None:
        raise HTTPException(503, "archive provider not initialized")

    accepted = await ctx.review_items_repo.list_by_clip(ctx.db, clip_id, decision="accepted")
    if not accepted:
        return {"applied": 0}

    annotation = await ctx.annotations_repo.get(ctx.db, accepted[0].annotation_id)
    template = await ctx.templates_repo.get(ctx.db, annotation.template_id)

    ops = _items_to_change_ops(accepted, template.target_map, fps=_fps_from_snapshot(annotation.clip_snapshot))
    if not ops:
        return {"applied": 0}

    change_set = ChangeSet(clip_key=("catdv", str(clip_id)), ops=tuple(ops))

    try:
        result = await ctx.archive.apply_changes(change_set)
    except ProviderError as exc:
        await ctx.write_log_repo.record(
            ctx.db,
            catdv_clip_id=clip_id,
            annotation_id=annotation.id,
            payload={"ops": [type(o).__name__ for o in ops]},
            response={"error": str(exc)},
            status="error",
        )
        raise HTTPException(502, f"archive apply failed: {exc}")

    await ctx.write_log_repo.record(
        ctx.db,
        catdv_clip_id=clip_id,
        annotation_id=annotation.id,
        payload={"ops": [type(o).__name__ for o in ops]},
        response=result.upstream_response,
        status="ok",
    )
    await ctx.review_items_repo.mark_applied(
        ctx.db,
        [it.id for it in accepted if it.id is not None],
    )
    return {"applied": len(accepted)}


def _fps_from_snapshot(snapshot: dict[str, Any]) -> float:
    v = snapshot.get("fps") if isinstance(snapshot, dict) else None
    if isinstance(v, (int, float)) and v > 0:
        return float(v)
    return 25.0


def _items_to_change_ops(
    items: list[ReviewItem],
    target_map: TargetMap,
    *,
    fps: float,
) -> list[ChangeOp]:
    ops: list[ChangeOp] = []
    new_markers: list[Marker] = []
    for it in items:
        value = it.edited_value if it.edited_value is not None else it.proposed_value
        if it.kind == "marker" and isinstance(value, dict):
            marker = _marker_from_review_value(value, fps)
            if marker is not None:
                new_markers.append(marker)
        elif it.kind == "field" and it.target_identifier:
            ops.append(SetField(identifier=it.target_identifier, value=_unwrap(value)))
        elif it.kind == "note" and it.target_identifier:
            mode = _note_mode(target_map, it.target_identifier)
            text = str(_unwrap(value))
            if mode == "replace":
                ops.append(ReplaceNote(target=it.target_identifier, text=text))
            else:
                ops.append(AppendNote(target=it.target_identifier, text=text))
    if new_markers:
        ops.insert(0, AddMarkers(markers=tuple(new_markers)))
    return ops


def _marker_from_review_value(value: dict[str, Any], fps: float) -> Marker | None:
    name = value.get("name")
    in_obj = value.get("in")
    if not isinstance(name, str) or not isinstance(in_obj, dict):
        return None
    in_secs = in_obj.get("secs")
    if not isinstance(in_secs, (int, float)):
        return None
    out_obj = value.get("out") if isinstance(value.get("out"), dict) else None
    out_tc = None
    if out_obj is not None and isinstance(out_obj.get("secs"), (int, float)):
        out_tc = Timecode(secs=float(out_obj["secs"]), fps=fps)
    return Marker(
        name=name,
        in_=Timecode(secs=float(in_secs), fps=fps),
        out=out_tc,
        description=value.get("description"),
        category=value.get("category"),
        color=value.get("color"),
    )


def _unwrap(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value and "evidence_secs" in value:
        return value["value"]
    return value


def _note_mode(target_map: TargetMap, identifier: str) -> str:
    for entry in target_map.fields.values():
        if entry.kind == "note" and entry.target == identifier:
            return entry.mode
    return "append"
```

- [ ] **Step 3: Run review route tests; fix breakage if any**

```bash
.venv/bin/pytest tests/integration/test_routes_review.py -v
```

If a test stubbed `ctx.catdv`, change it to stub `ctx.archive` with a fake that returns a `WriteResult(status="ok", upstream_response={...})`. The `write_log` payload field shape changed from "raw CatDV PUT body" to `{"ops": [...]}` — update assertions accordingly.

- [ ] **Step 4: Run full suite**

```bash
.venv/bin/pytest -q
```
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routes/review.py tests/integration/test_routes_review.py
git commit -m "refactor(routes/review): apply via archive.apply_changes + ChangeSet"
```

---

### Task 12: Switch annotator to use `ctx.archive`

**Files:**
- Modify: `backend/app/services/annotator.py`
- Modify: `backend/app/routes/jobs.py`
- Modify: `tests/integration/test_annotator_worker.py`

- [ ] **Step 1: Read the existing annotator + jobs test**

```bash
.venv/bin/pytest tests/integration/test_annotator_worker.py tests/integration/test_routes_jobs.py -v
```

Note the current pass count.

- [ ] **Step 2: Modify `services/annotator.py`**

Change `run_job` signature: drop `catdv` param, add `archive` param.

In `_process_item`, change:
```python
clip_snapshot: dict[str, Any] = await catdv.get_clip(item.catdv_clip_id)
```
to:
```python
canonical = await archive.get_clip(str(item.catdv_clip_id))
clip_snapshot: dict[str, Any] = dict(canonical.provider_data)
```

And update the `_process_item` signature: replace `catdv` parameter with `archive`. Threaded into the function from `run_job`.

Full updated calls in `run_job`:
```python
await _process_item(
    db=db,
    item=item,
    template=template,
    archive=archive,
    proxy_resolver=proxy_resolver,
    gcs=gcs,
    gemini=gemini,
    gcs_files_repo=gcs_files_repo,
    annotations_repo=annotations_repo,
    review_items_repo=review_items_repo,
    jobs_repo=jobs_repo,
    event_bus=event_bus,
    topic=topic,
)
```

- [ ] **Step 3: Modify `routes/jobs.py`**

In `_run_in_bg`:

```python
async def _run_in_bg(ctx, job_id: int) -> None:
    try:
        await run_job(
            db=ctx.db,
            job_id=job_id,
            archive=ctx.archive,
            proxy_resolver=ctx.proxy_resolver,
            gcs=ctx.gcs,
            gemini=ctx.gemini,
            event_bus=ctx.event_bus,
            gcs_files_repo=ctx.gcs_files_repo,
            annotations_repo=ctx.annotations_repo,
            review_items_repo=ctx.review_items_repo,
            jobs_repo=ctx.jobs_repo,
            templates_repo=ctx.templates_repo,
        )
    finally:
        ctx._running_jobs.pop(job_id, None)
```

In the readiness gate in `create_job`, replace `ctx.catdv` with `ctx.archive`:

```python
if body.auto_start and ctx.archive and ctx.gcs and ctx.gemini and ctx.proxy_resolver:
```

- [ ] **Step 4: Update `tests/integration/test_annotator_worker.py`**

Replace the existing `FakeCatdv` helper class with a fake archive that returns `CanonicalClip`:

```python
from datetime import datetime, timezone

from backend.app.archive.model import CanonicalClip, MediaRef


class FakeArchive:
    def __init__(self, clips: dict[int, dict]):
        self.clips = clips

    async def get_clip(self, clip_id_str: str) -> CanonicalClip:
        clip = self.clips[int(clip_id_str)]
        return CanonicalClip(
            key=("catdv", clip_id_str),
            name=clip.get("name", ""),
            duration_secs=0.0,
            fps=float(clip.get("fps") or 25.0),
            markers=tuple(),
            fields={},
            notes={},
            media=MediaRef(
                mime_type="video/quicktime",
                size_bytes=None,
                cached_path=None,
                upstream_handle=clip_id_str,
            ),
            provider_data=clip,
            fetched_at=datetime.now(timezone.utc),
        )
```

Then in both tests (`test_run_job_processes_two_clips_end_to_end` and `test_run_job_marks_item_error_when_gemini_raises`), replace:

```python
catdv = FakeCatdv({...})
```
with:
```python
archive = FakeArchive({...})
```

And the call:
```python
await run_job(
    ...
    catdv=catdv,
    ...
)
```
with:
```python
await run_job(
    ...
    archive=archive,
    ...
)
```

- [ ] **Step 5: Run tests, verify pass**

```bash
.venv/bin/pytest tests/integration/test_annotator_worker.py tests/integration/test_routes_jobs.py -v
```
Expected: all green.

- [ ] **Step 6: Run full suite**

```bash
.venv/bin/pytest -q
```
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/annotator.py backend/app/routes/jobs.py tests/integration/test_annotator_worker.py
git commit -m "refactor(annotator): consume CanonicalClip via archive.get_clip"
```

---

### Task 13: Delete the old `payload_builder.py`

**Files:**
- Delete: `backend/app/services/payload_builder.py`
- Delete: `tests/unit/test_payload_builder.py`

- [ ] **Step 1: Verify no remaining imports**

```bash
grep -RIn "from backend.app.services.payload_builder" backend tests
grep -RIn "services.payload_builder" backend tests
grep -RIn "import payload_builder" backend tests
```
Expected: no output.

If anything remains, that import must be migrated to `backend.app.archive.providers.catdv.payload` (different signature) before deletion.

- [ ] **Step 2: Delete the files**

```bash
rm backend/app/services/payload_builder.py tests/unit/test_payload_builder.py
```

- [ ] **Step 3: Run full suite**

```bash
.venv/bin/pytest -q
```
Expected: all green.

- [ ] **Step 4: Run ruff**

```bash
.venv/bin/ruff check backend tests
```
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add -A backend/app/services/payload_builder.py tests/unit/test_payload_builder.py
git commit -m "chore(archive): remove old services/payload_builder; superseded by adapter"
```

---

### Task 14: Smoke-check the running app (manual)

This task verifies the app boots and the read-only CatDV routes work end-to-end after the refactor. The user runs this; no code change.

- [ ] **Step 1: Bring up the dev server**

```bash
./run.sh
```

Expected: server starts at `localhost:8765`, no traceback referring to `payload_builder` or `ctx.catdv`.

- [ ] **Step 2: Sanity-check endpoints**

In a second shell:

```bash
curl -s http://localhost:8765/api/health
curl -s 'http://localhost:8765/api/catdv/clips?limit=2' | head -c 500
```

Expected: `{"status":"ok"}`, then a JSON list/page of clips (assuming VPN up and CATDV creds correct).

If the second curl returns a 503 "archive provider not initialized" — that means `init_external` didn't fire; check `.env`.

- [ ] **Step 3: Tear down**

Ctrl-C the server.

- [ ] **Step 4: Commit nothing**

This step is verification only. No commit.

---

## Self-review checklist (run after writing the plan, fix inline)

1. **Spec coverage** — Does this plan implement every bullet of spec §13 PR 1?
   - [x] `archive/` package with model + Protocol + CatDV adapter → Tasks 1, 2, 4, 5, 6, 7
   - [x] Wire `AppContext.archive` at startup → Task 9
   - [x] Refactor annotator/routes to use `ArchiveProvider` + `CanonicalClip` → Tasks 10, 11, 12
   - [x] `payload_builder` moves to `providers/catdv/payload.py` → Task 5 + delete in Task 13
   - [x] No new tables, `catdv_clip_id` columns unchanged → no migrations in this plan ✓
   - [x] App still talks live to CatDV on every Apply → Task 11 keeps Apply synchronous ✓
   - [x] One new test verifies adapter round-trips a recorded clip JSON → Task 3 fixture + Task 4 tests ✓

2. **Placeholder scan** — searched for "TBD", "TODO", "fill in", "appropriate error handling": none in this plan.

3. **Type consistency**
   - `ArchiveProvider.get_clip(clip: ProviderClipId)` — `ProviderClipId` is `str`. Routes pass `str(clip_id)`; annotator passes `str(item.catdv_clip_id)`; consistent.
   - `ChangeSet.clip_key` is `tuple[str, str]`. Routes/review build it as `("catdv", str(clip_id))`; consistent.
   - `WriteResult.upstream_response: dict` — `write_log.record(response=...)` accepts dict; consistent with current signature.
   - `CanonicalClip.provider_data: dict` — routes return it directly to UI; annotator copies it into `clip_snapshot`. Both treat it as raw dict; consistent.

4. **Scope check** — One subsystem, the archive port boundary. Each task is independently shippable and testable. ✓

---

## After this PR ships

PR 2 (AIInputStore port + GcsInputStore adapter) is the next plan to write. It cannot be planned in detail yet because the exact `AppContext` wiring and which lines of `annotator.py` need editing depend on what this PR's refactor settles. Start the next plan only after PR 1 is merged.
