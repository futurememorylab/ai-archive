from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.uploaded_clips import UploadedClipsRepo
from backend.app.uploaded_ids import to_clip_id


@pytest.fixture
async def conn(tmp_path: Path):
    cm = open_db(tmp_path / "t.db")
    c = await cm.__aenter__()
    await apply_migrations(c, Path("backend/migrations"))
    yield c
    await cm.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_create_sets_stored_filename_from_clip_id(conn):
    repo = UploadedClipsRepo()
    pk = await repo.create(
        conn, original_filename="My Clip.mp4", mime="video/mp4",
        size_bytes=123, ext=".mp4", duration_secs=12.5, width=1920, height=1080,
    )
    clip_id = to_clip_id(pk)
    row = await repo.get(conn, clip_id)
    assert row is not None
    assert row["original_filename"] == "My Clip.mp4"
    assert row["stored_filename"] == f"{clip_id}.mp4"
    assert row["mime"] == "video/mp4"
    assert row["duration_secs"] == 12.5


@pytest.mark.asyncio
async def test_get_missing_returns_none(conn):
    repo = UploadedClipsRepo()
    assert await repo.get(conn, 1_000_000_999) is None


@pytest.mark.asyncio
async def test_get_many_keyed_by_clip_id(conn):
    repo = UploadedClipsRepo()
    pk1 = await repo.create(conn, original_filename="a.mp4", mime="video/mp4",
                            size_bytes=1, ext=".mp4")
    pk2 = await repo.create(conn, original_filename="b.webm", mime="video/webm",
                            size_bytes=2, ext=".webm")
    got = await repo.get_many(conn, [to_clip_id(pk1), to_clip_id(pk2), 1_000_009_999])
    assert set(got) == {to_clip_id(pk1), to_clip_id(pk2)}
    assert got[to_clip_id(pk2)]["original_filename"] == "b.webm"


@pytest.mark.asyncio
async def test_delete(conn):
    repo = UploadedClipsRepo()
    pk = await repo.create(conn, original_filename="a.mp4", mime="video/mp4",
                           size_bytes=1, ext=".mp4")
    cid = to_clip_id(pk)
    await repo.delete(conn, cid)
    assert await repo.get(conn, cid) is None
