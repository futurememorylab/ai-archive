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

# The catalog the app lists against. Must match CATDV_CATALOG_ID in the app's
# boot env (app_server) and the catalog_id the seed writes into clip_list_cache,
# so the annotation-status filters resolve against the same clip universe.
CATALOG_ID = "881507"

# A clip that keeps a pending AI draft for the whole run — no scenario publishes
# it — so the annotation-status filter has a STABLE "awaiting review" member,
# independent of the review→publish scenario's mutations to clip 101.
REVIEW_FIXTURE_CLIP_ID = 110
REVIEW_FIXTURE_CLIP_NAME = "Reel awaiting human review"

# Search catalog: extra clips (beyond the canonical 101) so the search page can
# be driven end-to-end. The names are the single source of truth shared with the
# search scenarios — a search term, the clips that match it, and one that does
# not, so a scenario can assert "matches shown, non-match gone".
SEARCH_TERM = "Prague"
SEARCH_MATCH_NAMES = (
    "Prague rooftops at dawn",
    "Prague tram on Wenceslas Square",
)
SEARCH_NONMATCH_NAME = "Brno textile factory"
NO_RESULTS_TERM = "zzqxnomatch"
# (clip_id, name) for every extra clip. 101 is the canonical clip (build_clip)
# and is added separately; none of these names contain SEARCH_TERM by accident.
_EXTRA_CLIPS = (
    (102, SEARCH_MATCH_NAMES[0]),
    (103, SEARCH_MATCH_NAMES[1]),
    (104, SEARCH_NONMATCH_NAME),
    (105, "Wartime newsreel, reel 12"),
    (106, "Studio interview (unedited)"),
    (REVIEW_FIXTURE_CLIP_ID, REVIEW_FIXTURE_CLIP_NAME),
)
# A catalog clip that is never annotated — used by the "not annotated" filter
# scenario as the clip that should remain visible.
NOT_ANNOTATED_CLIP_NAME = SEARCH_NONMATCH_NAME


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


def _list_clip(
    clip_id: int, name: str, video_path: Path, duration_secs: float = 8.0, fps: float = 25.0
) -> CanonicalClip:
    """A minimal clip for the list/search surface (no markers/draft needed)."""
    return CanonicalClip(
        key=("catdv", str(clip_id)),
        name=name,
        duration_secs=duration_secs,
        fps=fps,
        markers=(),
        fields={},
        notes={},
        media=MediaRef(
            mime_type="video/mp4",
            size_bytes=video_path.stat().st_size,
            cached_path=video_path,
            upstream_handle=str(clip_id),
        ),
        provider_data={"ID": clip_id, "name": name},
        fetched_at=datetime.now(UTC),
    )


def build_clips(video_path: Path) -> tuple[CanonicalClip, ...]:
    """The full in-memory catalog: the canonical clip 101 + the search extras."""
    return (build_clip(video_path), *(_list_clip(i, n, video_path) for i, n in _EXTRA_CLIPS))


class FakeArchive:
    """Numeric-keyed archive serving an in-memory clip catalog.

    `list_clips` filters by `query.text` (case-insensitive substring of the clip
    name, matching the real list's Python-side name filter) and paginates with
    offset/limit, so the search page is fully driveable with no real provider.
    Accepts a single clip or a sequence. Records apply_changes.
    """

    def __init__(self, clips: CanonicalClip | tuple[CanonicalClip, ...]) -> None:
        self._clips: tuple[CanonicalClip, ...] = (
            (clips,) if isinstance(clips, CanonicalClip) else tuple(clips)
        )
        self.applied: list = []

    async def list_clips(self, catalog, query):
        needle = (query.text or "").strip().casefold()
        matches = [c for c in self._clips if needle in c.name.casefold()] if needle else list(self._clips)
        page = matches[query.offset : query.offset + query.limit]
        return ClipPage(items=tuple(page), total=len(matches), offset=query.offset, limit=query.limit)

    async def get_clip(self, clip_id_str: str):
        for clip in self._clips:
            if clip_id_str == clip.key[1]:
                return clip
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
    """Serves one real JPEG poster for every clip so rows show a thumbnail on
    camera instead of a broken-image placeholder. Offline (never hits network).
    Falls back to a cache miss (None) when no poster path was supplied."""

    is_online_provider = False

    def __init__(self, thumb_path: Path | None = None) -> None:
        self._thumb = thumb_path

    async def get_or_fetch(self, clip_id: int):
        return self._thumb
