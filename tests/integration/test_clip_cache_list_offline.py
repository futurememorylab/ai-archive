from datetime import UTC, datetime

import pytest

from backend.app.archive.model import CanonicalClip, MediaRef
from backend.app.repositories.clip_cache import ClipCacheRepo


def _clip(name: str, notes: str = "", clip_id: str = "1") -> CanonicalClip:
    return CanonicalClip(
        key=("catdv", clip_id),
        name=name,
        duration_secs=10.0,
        fps=25.0,
        markers=(),
        fields={},
        notes={"notes": notes},
        media=MediaRef(
            mime_type="video/quicktime",
            size_bytes=0,
            cached_path=None,
            upstream_handle=clip_id,
        ),
        provider_data={},
        fetched_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_paginated_list_returns_items_and_total(db):
    repo = ClipCacheRepo()
    for i in range(5):
        await repo.upsert(db, clip=_clip(f"Clip {i}", clip_id=str(i)), catalog_id="881507")

    items, total = await repo.list_by_catalog(
        db,
        provider_id="catdv",
        catalog_id="881507",
        offset=0,
        limit=2,
        q=None,
        canonical=True,
    )
    assert total == 5
    assert [c.name for c in items] == ["Clip 0", "Clip 1"]


@pytest.mark.asyncio
async def test_search_matches_name_case_insensitive(db):
    repo = ClipCacheRepo()
    await repo.upsert(db, clip=_clip("alpha", clip_id="1"), catalog_id="881507")
    await repo.upsert(db, clip=_clip("BETA", clip_id="2"), catalog_id="881507")
    await repo.upsert(db, clip=_clip("gamma", clip_id="3"), catalog_id="881507")

    items, total = await repo.list_by_catalog(
        db,
        provider_id="catdv",
        catalog_id="881507",
        offset=0,
        limit=10,
        q="bet",
        canonical=True,
    )
    assert total == 1
    assert items[0].name == "BETA"


@pytest.mark.asyncio
async def test_search_matches_notes(db):
    repo = ClipCacheRepo()
    await repo.upsert(
        db, clip=_clip("nope", notes="needle in here", clip_id="1"), catalog_id="881507"
    )
    await repo.upsert(db, clip=_clip("other", notes="haystack", clip_id="2"), catalog_id="881507")

    items, total = await repo.list_by_catalog(
        db,
        provider_id="catdv",
        catalog_id="881507",
        offset=0,
        limit=10,
        q="needle",
        canonical=True,
    )
    assert total == 1
    assert items[0].name == "nope"


@pytest.mark.asyncio
async def test_catalog_filter_excludes_other_catalogs(db):
    repo = ClipCacheRepo()
    await repo.upsert(db, clip=_clip("mine", clip_id="1"), catalog_id="881507")
    await repo.upsert(db, clip=_clip("theirs", clip_id="2"), catalog_id="999999")

    items, total = await repo.list_by_catalog(
        db,
        provider_id="catdv",
        catalog_id="881507",
        offset=0,
        limit=10,
        q=None,
        canonical=True,
    )
    assert total == 1
    assert items[0].name == "mine"


@pytest.mark.asyncio
async def test_legacy_call_returns_raw_rows_unchanged(db):
    """Existing CacheInspector.deep_orphans still calls the no-kwarg shape."""
    repo = ClipCacheRepo()
    await repo.upsert(db, clip=_clip("x", clip_id="1"), catalog_id="881507")
    rows = await repo.list_by_catalog(db, provider_id="catdv", catalog_id="881507")
    assert isinstance(rows, list) and len(rows) == 1
    assert rows[0]["provider_clip_id"] == "1"
