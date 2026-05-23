"""FilesystemArchiveProvider: ArchiveProvider against a `FS_ROOT` tree.

Directory layout (see `docs/fs-archive-format.md`):

    <FS_ROOT>/
      .archive/
        fields.json                 # optional: provider field defs
      <catalog>/
        <clip>.mov                  # one of FS_MEDIA_EXTS
        <clip>.annot.json           # optional sidecar with annotations

`provider_clip_id` is the catalog-prefixed path with the media extension
stripped — e.g. `archive_30s/clip001` for `<FS_ROOT>/archive_30s/clip001.mov`.
Subdirectories nest into `provider_clip_id` but do NOT count as separate
catalogs (`list_catalogs()` is one-level only). See `docs/decisions.md`
for the full rationale.

Writes are POSIX-atomic (tempfile + fsync + `os.replace`) and use sha256
of the sidecar bytes as a real optimistic-concurrency etag.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from backend.app.archive.errors import FatalProviderError
from backend.app.archive.model import (
    AddMarkers,
    AppendNote,
    CanonicalClip,
    ChangeSet,
    ClipPage,
    ClipQuery,
    ConflictDetail,
    FieldDef,
    FieldValue,
    Marker,
    MediaRef,
    ReplaceNote,
    SetField,
    WriteResult,
)
from backend.app.archive.provider import ProviderCapabilities, ProviderHealth
from backend.app.archive.providers.fs import fields as fields_loader
from backend.app.archive.providers.fs import media_probe
from backend.app.archive.providers.fs.sidecar import (
    SIDECAR_TOP_KEYS,
    dumps_sidecar,
    loads_sidecar,
    parse_sidecar,
    render_sidecar,
)

log = logging.getLogger(__name__)


DEFAULT_MEDIA_EXTS: tuple[str, ...] = (
    ".mov",
    ".mp4",
    ".mkv",
    ".mxf",
    ".m4v",
    ".avi",
)


def _normalise_exts(exts: Iterable[str]) -> tuple[str, ...]:
    out: list[str] = []
    for e in exts:
        e = e.strip().lower()
        if not e:
            continue
        if not e.startswith("."):
            e = "." + e
        out.append(e)
    return tuple(out) if out else DEFAULT_MEDIA_EXTS


class FilesystemArchiveProvider:
    id = "fs"

    capabilities = ProviderCapabilities(
        supports_markers=True,
        supports_notes=frozenset({"notes", "bigNotes"}),
        supports_field_create=False,
        supports_etag=True,
        media_is_local=True,
        write_atomicity="per-clip",
    )

    def __init__(
        self,
        *,
        fs_root: Path,
        media_exts: Sequence[str] | None = None,
        clock: Callable[[], datetime] | None = None,
        # Accept-but-ignore parameters so the registry can pass the same
        # bag of kwargs as the CatDV adapter; see decision 6.
        clip_cache_repo: Any = None,
        field_def_cache_repo: Any = None,
        db_provider: Callable[[], Any] | None = None,
    ) -> None:
        self._root = Path(fs_root).resolve()
        self._exts = _normalise_exts(media_exts or DEFAULT_MEDIA_EXTS)
        self._clock = clock or (lambda: datetime.now(UTC))
        # Unused but stored to match the CatDV adapter's interface for
        # any future cache-aware FS feature.
        self._clip_cache = clip_cache_repo
        self._field_def_cache = field_def_cache_repo
        self._db_provider = db_provider

    # --- path resolution --------------------------------------------------

    def _clip_id_for_media(self, media_path: Path) -> str:
        rel = media_path.relative_to(self._root)
        rel_no_ext = rel.with_suffix("")
        # Always use forward slashes in stored ids, regardless of host OS.
        return str(rel_no_ext).replace(os.sep, "/")

    def _media_path_for_clip_id(self, provider_clip_id: str) -> Path:
        rel = provider_clip_id.replace("/", os.sep)
        base = self._root / rel
        # Look for any of the configured extensions, case-insensitively.
        for ext in self._exts:
            candidate = base.with_suffix(ext)
            if candidate.exists():
                return candidate
            candidate_upper = base.with_suffix(ext.upper())
            if candidate_upper.exists():
                return candidate_upper
        # Fall back: scan the parent for a stem match (handles e.g. .MOV).
        parent = base.parent
        if parent.is_dir():
            stem = base.name
            for child in parent.iterdir():
                if child.is_file() and child.stem == stem and self._is_media_file(child):
                    return child
        raise FatalProviderError(
            f"FS adapter: no media file found for {provider_clip_id!r} under {self._root}"
        )

    def _sidecar_path(self, media_path: Path) -> Path:
        return media_path.with_name(media_path.stem + ".annot.json")

    def _is_media_file(self, path: Path) -> bool:
        return path.suffix.lower() in self._exts

    # --- health -----------------------------------------------------------

    async def health(self) -> ProviderHealth:
        t0 = perf_counter()
        if not self._root.exists():
            return ProviderHealth(ok=False, detail=f"FS_ROOT not found: {self._root}")
        if not self._root.is_dir():
            return ProviderHealth(ok=False, detail=f"FS_ROOT not a directory: {self._root}")
        try:
            # Touch the root to confirm readability.
            list(self._root.iterdir())
        except OSError as exc:
            return ProviderHealth(ok=False, detail=f"FS_ROOT not readable: {exc}")
        fields_path = self._root / ".archive" / "fields.json"
        if fields_path.exists():
            try:
                fields_path.read_text(encoding="utf-8")
            except OSError as exc:
                return ProviderHealth(ok=False, detail=f"fields.json unreadable: {exc}")
        latency_ms = (perf_counter() - t0) * 1000.0
        return ProviderHealth(ok=True, latency_ms=latency_ms)

    # --- catalogs ---------------------------------------------------------

    async def list_catalogs(self) -> list[dict[str, str]]:
        if not self._root.is_dir():
            return []
        out: list[dict[str, str]] = []
        for child in sorted(self._root.iterdir()):
            if not child.is_dir():
                continue
            if child.name.startswith("."):
                continue
            out.append({"id": child.name, "name": child.name})
        return out

    # --- list / get -------------------------------------------------------

    async def list_clips(self, catalog: str, query: ClipQuery) -> ClipPage:
        cat_dir = self._root / catalog
        if not cat_dir.is_dir():
            return ClipPage(items=(), total=0, offset=query.offset, limit=query.limit)

        media_files: list[Path] = []
        for path in sorted(cat_dir.rglob("*")):
            if path.is_file() and self._is_media_file(path):
                media_files.append(path)

        text = (query.text or "").strip().lower()
        if text:
            media_files = [p for p in media_files if text in p.stem.lower()]

        total = len(media_files)
        page_slice = media_files[query.offset : query.offset + query.limit]
        clips = tuple(self._build_canonical_clip(p) for p in page_slice)
        return ClipPage(
            items=clips,
            total=total,
            offset=query.offset,
            limit=query.limit,
        )

    async def get_clip(self, clip: str) -> CanonicalClip:
        media_path = self._media_path_for_clip_id(clip)
        return self._build_canonical_clip(media_path)

    def _build_canonical_clip(self, media_path: Path) -> CanonicalClip:
        clip_id = self._clip_id_for_media(media_path)
        duration_secs, fps = media_probe.probe(media_path)
        sidecar = self._read_sidecar(self._sidecar_path(media_path))
        markers, fields, notes, provider_data = parse_sidecar(sidecar, default_fps=fps)

        try:
            size_bytes: int | None = media_path.stat().st_size
        except OSError:
            size_bytes = None

        mime = _mime_for_ext(media_path.suffix)
        media = MediaRef(
            mime_type=mime,
            size_bytes=size_bytes,
            cached_path=media_path,
            upstream_handle=str(media_path),
        )

        # Carry diagnostic bits in provider_data without clobbering the
        # round-trip slots reserved for unknown sidecar keys.
        if "ffprobe_present" not in provider_data:
            import shutil as _shutil

            provider_data = {
                **provider_data,
                "ffprobe_present": _shutil.which("ffprobe") is not None,
            }

        return CanonicalClip(
            key=(self.id, clip_id),
            name=media_path.stem,
            duration_secs=duration_secs,
            fps=fps,
            markers=markers,
            fields=fields,
            notes=notes,
            media=media,
            provider_data=provider_data,
            fetched_at=self._clock(),
        )

    # --- field defs -------------------------------------------------------

    async def list_field_definitions(self) -> list[FieldDef]:
        return fields_loader.load_field_defs(self._root)

    # --- write API --------------------------------------------------------

    async def apply_changes(self, change_set: ChangeSet) -> WriteResult:
        provider_id, clip_id = change_set.clip_key
        if provider_id != self.id:
            raise FatalProviderError(f"ChangeSet for provider {provider_id!r} sent to fs adapter")
        media_path = self._media_path_for_clip_id(clip_id)
        sidecar_path = self._sidecar_path(media_path)

        # Read the live state + compute its etag.
        live_bytes = self._read_sidecar_bytes(sidecar_path)
        live_etag = _etag_for_bytes(live_bytes)

        if change_set.expected_etag is not None and live_etag != change_set.expected_etag:
            return WriteResult(
                status="conflict",
                upstream_response={},
                new_etag=live_etag,
                conflict_detail=ConflictDetail(
                    kind="modified",
                    expected_etag=change_set.expected_etag,
                    actual_etag=live_etag,
                ),
            )

        # Decode → mutate → render.
        raw = loads_sidecar(live_bytes.decode("utf-8")) if live_bytes else None
        _, fps = media_probe.probe(media_path)
        markers, fields, notes, provider_data = parse_sidecar(raw, default_fps=fps)
        markers_list: list[Marker] = list(markers)
        fields_dict: dict[str, FieldValue] = dict(fields)
        notes_dict: dict[str, str] = dict(notes)

        for op in change_set.ops:
            if isinstance(op, AddMarkers):
                markers_list.extend(op.markers)
            elif isinstance(op, SetField):
                fields_dict[op.identifier] = FieldValue(
                    identifier=op.identifier,
                    value=op.value,
                    is_multi=isinstance(op.value, list),
                )
            elif isinstance(op, AppendNote):
                existing = notes_dict.get(op.target, "")
                notes_dict[op.target] = existing + ("\n" if existing else "") + op.text
            elif isinstance(op, ReplaceNote):
                notes_dict[op.target] = op.text
            else:  # pragma: no cover — exhaustive ChangeOp union
                raise FatalProviderError(f"unknown ChangeOp: {type(op).__name__}")

        new_doc = render_sidecar(
            markers=tuple(markers_list),
            fields=fields_dict,
            notes=notes_dict,
            provider_data=provider_data,
        )
        new_text = dumps_sidecar(new_doc)
        new_bytes = new_text.encode("utf-8")

        _atomic_write(sidecar_path, new_bytes)

        return WriteResult(
            status="ok",
            upstream_response={"sidecar_path": str(sidecar_path)},
            new_etag=_etag_for_bytes(new_bytes),
        )

    # --- helpers ----------------------------------------------------------

    def _read_sidecar_bytes(self, path: Path) -> bytes:
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return b""
        except OSError as exc:
            raise FatalProviderError(f"sidecar unreadable {path}: {exc}") from exc

    def _read_sidecar(self, path: Path) -> dict[str, Any] | None:
        data = self._read_sidecar_bytes(path)
        if not data:
            return None
        try:
            return loads_sidecar(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            log.warning("sidecar %s malformed; treating as empty: %s", path, exc)
            return None


# --- module helpers -------------------------------------------------------


def _etag_for_bytes(data: bytes) -> str | None:
    if not data:
        return None
    return hashlib.sha256(data).hexdigest()


def _atomic_write(path: Path, data: bytes) -> None:
    """Write `data` to `path` atomically (tempfile + fsync + rename).

    The tempfile is created in the same directory so the final
    `os.replace` is a single syscall on the same filesystem (POSIX
    rename is atomic within a fs). On exception before rename, the
    tempfile is cleaned up; the existing on-disk sidecar is untouched.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except Exception:
        # If the rename succeeded, tmp_path no longer exists; the
        # missing_ok=True swallows that benign case.
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


_MIME_BY_EXT: dict[str, str] = {
    ".mov": "video/quicktime",
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mkv": "video/x-matroska",
    ".mxf": "application/mxf",
    ".avi": "video/x-msvideo",
}


def _mime_for_ext(ext: str) -> str:
    return _MIME_BY_EXT.get(ext.lower(), "video/quicktime")


# Re-export the sidecar top-key set for tests that exercise the round-trip.
__all__ = [
    "DEFAULT_MEDIA_EXTS",
    "FilesystemArchiveProvider",
    "SIDECAR_TOP_KEYS",
]
