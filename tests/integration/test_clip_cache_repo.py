from datetime import datetime, timezone

import pytest

from backend.app.archive.model import (
    CanonicalClip,
    FieldValue,
    Marker,
    MediaRef,
    Timecode,
)
from backend.app.repositories.clip_cache import ClipCacheRepo


def _make_clip(clip_id: str = "1", *, name: str = "Clip_1") -> CanonicalClip:
    return CanonicalClip(
        key=("catdv", clip_id),
        name=name,
        duration_secs=12.5,
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
        provider_data={"ID": int(clip_id), "fps": 25.0},
        fetched_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_upsert_and_get_round_trips_canonical_clip(db):
    repo = ClipCacheRepo()
    clip = _make_clip("1", name="Clip_A")
    await repo.upsert(db, clip=clip, catalog_id="881507")

    got = await repo.get_by_key(db, provider_id="catdv", provider_clip_id="1")
    assert got is not None
    assert got.key == ("catdv", "1")
    assert got.name == "Clip_A"
    assert got.fps == 25.0
    assert got.markers[0].name == "m"
    assert got.fields["pragafilm.barva"].value == "true"
    assert got.notes == {"notes": "n"}
    assert got.provider_data == {"ID": 1, "fps": 25.0}


@pytest.mark.asyncio
async def test_get_returns_none_when_absent(db):
    repo = ClipCacheRepo()
    assert await repo.get_by_key(
        db, provider_id="catdv", provider_clip_id="404"
    ) is None


@pytest.mark.asyncio
async def test_get_row_returns_metadata(db):
    repo = ClipCacheRepo()
    clip = _make_clip("2")
    await repo.upsert(db, clip=clip, catalog_id="881507", provider_etag="W/1")
    row = await repo.get_row(db, provider_id="catdv", provider_clip_id="2")
    assert row is not None
    assert row["catalog_id"] == "881507"
    assert row["provider_etag"] == "W/1"
    assert row["fetched_at"] is not None


@pytest.mark.asyncio
async def test_upsert_replaces_on_same_pk(db):
    repo = ClipCacheRepo()
    await repo.upsert(db, clip=_make_clip("3", name="v1"), catalog_id="c1")
    await repo.upsert(db, clip=_make_clip("3", name="v2"), catalog_id="c1")
    got = await repo.get_by_key(db, provider_id="catdv", provider_clip_id="3")
    assert got is not None and got.name == "v2"


@pytest.mark.asyncio
async def test_list_by_catalog(db):
    repo = ClipCacheRepo()
    await repo.upsert(db, clip=_make_clip("4"), catalog_id="A")
    await repo.upsert(db, clip=_make_clip("5"), catalog_id="A")
    await repo.upsert(db, clip=_make_clip("6"), catalog_id="B")
    rows = await repo.list_by_catalog(db, provider_id="catdv", catalog_id="A")
    keys = {(r["provider_id"], r["provider_clip_id"]) for r in rows}
    assert keys == {("catdv", "4"), ("catdv", "5")}


@pytest.mark.asyncio
async def test_delete_by_key(db):
    repo = ClipCacheRepo()
    await repo.upsert(db, clip=_make_clip("7"), catalog_id="A")
    await repo.delete_by_key(db, provider_id="catdv", provider_clip_id="7")
    assert await repo.get_by_key(
        db, provider_id="catdv", provider_clip_id="7"
    ) is None
