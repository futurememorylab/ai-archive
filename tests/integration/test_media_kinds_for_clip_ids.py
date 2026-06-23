"""media_kinds_for_clip_ids: DB-first media-kind lookup, offline-safe.

Image-extension clips classify as 'image' (HIGH-eligible); video clips as
'video+audio'; uncached ids default to the safe non-image 'video+audio'.
"""

from datetime import UTC, datetime

import pytest

from backend.app.archive.model import CanonicalClip, MediaRef
from backend.app.repositories.clip_cache import ClipCacheRepo
from backend.app.services.run_estimator import media_kinds_for_clip_ids


async def _seed(db, repo: ClipCacheRepo, clip_id: int, handle: str) -> None:
    clip = CanonicalClip(
        key=("catdv", str(clip_id)),
        name=f"clip {clip_id}",
        duration_secs=12.5,
        fps=25.0,
        markers=(),
        fields={},
        notes={},
        media=MediaRef(
            mime_type=None,
            size_bytes=None,
            cached_path=None,
            upstream_handle=handle,
        ),
        provider_data={},
        fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    await repo.upsert(db, clip=clip, catalog_id="test-catalog")


@pytest.mark.asyncio
async def test_media_kinds_classifies_by_extension(db):
    repo = ClipCacheRepo()
    await _seed(db, repo, 1, "photo.jpg")
    await _seed(db, repo, 2, "shot.mov")

    kinds = await media_kinds_for_clip_ids(
        db, clip_cache_repo=repo, provider_id="catdv", clip_ids=[1, 2, 999]
    )
    assert kinds[1] == "image"
    assert kinds[2] == "video+audio"  # .mov has no audio hint → safe default
    assert kinds[999] == "video+audio"  # uncached → safe non-image default
