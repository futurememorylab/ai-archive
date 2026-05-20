from datetime import datetime, timezone

import pytest

from backend.app.archive.model import (
    CanonicalClip,
    FieldValue,
    Marker,
    MediaRef,
    Timecode,
)
from backend.app.repositories.clip_list_cache import ClipListCacheRepo


def _make_clip(clip_id: str, *, name: str = "Clip") -> CanonicalClip:
    return CanonicalClip(
        key=("catdv", clip_id),
        name=name,
        duration_secs=10.0,
        fps=25.0,
        markers=(Marker(name="m", in_=Timecode(secs=1.0, fps=25.0), out=None),),
        fields={"pragafilm.barva": FieldValue("pragafilm.barva", "true")},
        notes={"notes": "n"},
        media=MediaRef(
            mime_type="video/quicktime",
            size_bytes=None,
            cached_path=None,
            upstream_handle=clip_id,
        ),
        provider_data={"ID": int(clip_id)},
        fetched_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_upsert_and_get_round_trip(db):
    repo = ClipListCacheRepo()
    items = (_make_clip("1", name="A"), _make_clip("2", name="B"))
    await repo.upsert(
        db,
        provider_id="catdv",
        catalog_id="881507",
        query_text=None,
        offset=0,
        limit=50,
        total=2,
        items=items,
        fetched_at_iso="2026-05-19T10:00:00+00:00",
    )

    page = await repo.get(
        db,
        provider_id="catdv",
        catalog_id="881507",
        query_text=None,
        offset=0,
        limit=50,
    )
    assert page is not None
    assert page["total"] == 2
    assert page["fetched_at"] == "2026-05-19T10:00:00+00:00"
    assert tuple(c.name for c in page["items"]) == ("A", "B")
    assert page["items"][0].markers[0].name == "m"


@pytest.mark.asyncio
async def test_get_misses_on_different_key(db):
    repo = ClipListCacheRepo()
    await repo.upsert(
        db,
        provider_id="catdv",
        catalog_id="881507",
        query_text=None,
        offset=0,
        limit=50,
        total=0,
        items=(),
        fetched_at_iso="2026-05-19T10:00:00+00:00",
    )
    assert await repo.get(
        db, provider_id="catdv", catalog_id="881507",
        query_text="foo", offset=0, limit=50,
    ) is None
    assert await repo.get(
        db, provider_id="catdv", catalog_id="881507",
        query_text=None, offset=50, limit=50,
    ) is None
    assert await repo.get(
        db, provider_id="catdv", catalog_id="OTHER",
        query_text=None, offset=0, limit=50,
    ) is None


@pytest.mark.asyncio
async def test_upsert_replaces_same_key(db):
    repo = ClipListCacheRepo()
    await repo.upsert(
        db, provider_id="catdv", catalog_id="881507", query_text=None,
        offset=0, limit=50, total=1, items=(_make_clip("1", name="v1"),),
        fetched_at_iso="2026-05-19T10:00:00+00:00",
    )
    await repo.upsert(
        db, provider_id="catdv", catalog_id="881507", query_text=None,
        offset=0, limit=50, total=1, items=(_make_clip("1", name="v2"),),
        fetched_at_iso="2026-05-19T11:00:00+00:00",
    )
    page = await repo.get(
        db, provider_id="catdv", catalog_id="881507",
        query_text=None, offset=0, limit=50,
    )
    assert page is not None
    assert page["items"][0].name == "v2"
    assert page["fetched_at"] == "2026-05-19T11:00:00+00:00"


@pytest.mark.asyncio
async def test_invalidate_catalog_wipes_only_that_catalog(db):
    repo = ClipListCacheRepo()
    await repo.upsert(
        db, provider_id="catdv", catalog_id="A", query_text=None,
        offset=0, limit=50, total=0, items=(),
        fetched_at_iso="2026-05-19T10:00:00+00:00",
    )
    await repo.upsert(
        db, provider_id="catdv", catalog_id="A", query_text="q",
        offset=0, limit=50, total=0, items=(),
        fetched_at_iso="2026-05-19T10:00:00+00:00",
    )
    await repo.upsert(
        db, provider_id="catdv", catalog_id="B", query_text=None,
        offset=0, limit=50, total=0, items=(),
        fetched_at_iso="2026-05-19T10:00:00+00:00",
    )

    removed = await repo.invalidate_catalog(
        db, provider_id="catdv", catalog_id="A"
    )
    assert removed == 2
    assert await repo.get(
        db, provider_id="catdv", catalog_id="A",
        query_text=None, offset=0, limit=50,
    ) is None
    assert await repo.get(
        db, provider_id="catdv", catalog_id="B",
        query_text=None, offset=0, limit=50,
    ) is not None
