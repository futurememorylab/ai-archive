"""get_many_by_ids: batched read via chunked_in_clause; constant query
count regardless of key-list size (ADR 0046)."""

from datetime import UTC, datetime

import pytest

from backend.app.archive.model import (
    CanonicalClip,
    MediaRef,
)
from backend.app.repositories.clip_cache import ClipCacheRepo
from tests._helpers.query_count import assert_query_count


async def _seed(db, repo: ClipCacheRepo, clip_id: int) -> None:
    clip = CanonicalClip(
        key=("catdv", str(clip_id)),
        name=f"clip {clip_id}",
        duration_secs=12.5,
        fps=25.0,
        markers=(),
        fields={},
        notes={},
        media=MediaRef(
            mime_type="video/quicktime",
            size_bytes=None,
            cached_path=None,
            upstream_handle=str(clip_id),
        ),
        provider_data={},
        fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    await repo.upsert(db, clip=clip, catalog_id="test-catalog")


@pytest.mark.asyncio
async def test_get_many_returns_rows(db):
    repo = ClipCacheRepo()
    for cid in (1, 2, 3):
        await _seed(db, repo, cid)
    rows = await repo.get_many_by_ids(db, "catdv", [1, 3, 999])
    assert set(rows) == {1, 3}
    assert rows[1]["duration_secs"] == 12.5
    assert rows[1]["canonical_json"]["media"]["mime_type"] == "video/quicktime"


@pytest.mark.asyncio
async def test_get_many_constant_query_count(db):
    repo = ClipCacheRepo()
    for cid in range(1, 30):
        await _seed(db, repo, cid)
    async with assert_query_count(db, 1):
        await repo.get_many_by_ids(db, "catdv", list(range(1, 11)))
    async with assert_query_count(db, 1):
        await repo.get_many_by_ids(db, "catdv", list(range(1, 30)))


@pytest.mark.asyncio
async def test_get_many_empty_list(db):
    rows = await ClipCacheRepo().get_many_by_ids(db, "catdv", [])
    assert rows == {}


@pytest.mark.asyncio
async def test_get_many_multi_chunk(db, monkeypatch):
    """29 keys with chunk_size=10 → 3 chunks → 3 queries, results merged."""
    import functools

    from backend.app.repositories import _batch, clip_cache as clip_cache_mod

    repo = ClipCacheRepo()
    for cid in range(1, 30):
        await _seed(db, repo, cid)
    monkeypatch.setattr(
        clip_cache_mod,
        "chunked_in_clause",
        functools.partial(_batch.chunked_in_clause, chunk_size=10),
    )
    async with assert_query_count(db, 3):
        rows = await repo.get_many_by_ids(db, "catdv", list(range(1, 30)))
    assert set(rows) == set(range(1, 30))
