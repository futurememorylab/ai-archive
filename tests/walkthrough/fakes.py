"""Walkthrough-local archive/resolver/thumbnail doubles.

Mirrors tests/integration/test_clip_detail_draft.py: the web UI requires a
numeric clip key (ui/view_models.py does int(clip.key[1])), which the real fs
provider cannot supply. These are injected via install_live_ctx.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from backend.app.archive.errors import ProviderError
from backend.app.archive.model import (
    CanonicalClip,
    ClipPage,
    FieldValue,
    Marker,
    MediaRef,
    Timecode,
)

CLIP_ID = 101
CLIP_NAME = "archive_30s"
DECADE_IDENT = "pragafilm.dekáda.natočení"
PUBLISHED_DECADE = "30.léta"


def build_clip(video_path: Path, duration_secs: float = 8.0, fps: float = 25.0) -> CanonicalClip:
    """The single clip the walkthrough renders. Published state lives here."""
    return CanonicalClip(
        key=("catdv", str(CLIP_ID)),
        name=CLIP_NAME,
        duration_secs=duration_secs,
        fps=fps,
        markers=(
            Marker(
                name="intro",
                in_=Timecode(secs=0.0, fps=fps, frm=0),
                out=Timecode(secs=2.0, fps=fps, frm=50),
                description="Opening title card",
            ),
        ),
        fields={DECADE_IDENT: FieldValue(identifier=DECADE_IDENT, value=PUBLISHED_DECADE)},
        notes={"notes": "Test clip"},
        media=MediaRef(
            mime_type="video/mp4",
            size_bytes=video_path.stat().st_size,
            cached_path=video_path,
            upstream_handle=str(CLIP_ID),
        ),
        provider_data={"ID": CLIP_ID, "name": CLIP_NAME},
        fetched_at=datetime.now(UTC),
    )


class FakeArchive:
    """Numeric-keyed archive serving exactly one clip. Records apply_changes."""

    def __init__(self, clip: CanonicalClip) -> None:
        self._clip = clip
        self.applied: list = []

    async def list_clips(self, catalog, query):
        return ClipPage(items=(self._clip,), total=1, offset=query.offset, limit=query.limit)

    async def get_clip(self, clip_id_str: str):
        if clip_id_str == self._clip.key[1]:
            return self._clip
        raise ProviderError(f"clip not found: {clip_id_str}")

    async def apply_changes(self, change_set):
        # MVP: record the attempt. The durable write-queue rows are the real
        # receipt for "publish happened"; no upstream write is performed.
        self.applied.append(change_set)
        from backend.app.archive.model import WriteResult

        return WriteResult(status="ok", upstream_response={}, new_etag="fake-etag")


class LocalFileResolver:
    """Returns a real on-disk video so /api/media/{id} streams a playable file."""

    is_host_local = False

    def __init__(self, video_path: Path) -> None:
        self._video = video_path

    async def path_for_clip_id(self, clip_id: int) -> Path:
        return self._video

    def is_managed(self, path: Path) -> bool:
        return True


class StubThumbnailService:
    """Offline-safe: always a cache miss → UI renders a placeholder."""

    is_online_provider = False

    async def get_or_fetch(self, clip_id: int):
        return None
