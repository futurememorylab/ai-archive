from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.uploaded_clips import UploadedClipsRepo
from backend.app.services.annotator import _resolve_clip_meta
from backend.app.uploaded_ids import to_clip_id


class _FakeClip:
    name = "Archive Clip"
    duration_secs = 30.0
    fps = 25.0
    provider_data = {"media": {"filePath": "/x/clip.mov"}}

    class media:  # noqa: N801
        cached_path = "/x/clip.mov"
        upstream_handle = None
        size_bytes = 9999


class _FakeArchive:
    async def get_clip(self, clip_id):
        return _FakeClip()


@pytest.fixture
async def conn(tmp_path):
    cm = open_db(tmp_path / "t.db")
    c = await cm.__aenter__()
    await apply_migrations(c, Path("backend/migrations"))
    yield c
    await cm.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_archive_branch(conn):
    meta = await _resolve_clip_meta(
        conn, clip_id=42, archive=_FakeArchive(), uploaded_clips_repo=UploadedClipsRepo()
    )
    assert meta.clip_key == ("catdv", "42")
    assert meta.duration_secs == 30.0
    assert meta.clip_name == "Archive Clip"


@pytest.mark.asyncio
async def test_uploaded_branch(conn):
    repo = UploadedClipsRepo()
    pk = await repo.create(conn, original_filename="up.mp4", mime="video/mp4",
                           size_bytes=10, ext=".mp4", duration_secs=8.0)
    cid = to_clip_id(pk)
    meta = await _resolve_clip_meta(
        conn, clip_id=cid, archive=_FakeArchive(), uploaded_clips_repo=repo
    )
    assert meta.clip_key == ("uploaded", str(cid))
    assert meta.duration_secs == 8.0
    assert meta.media_kind == "video"
    assert meta.clip_name == "up.mp4"
