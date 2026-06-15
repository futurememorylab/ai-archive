"""UploadCleanup — orphan garbage-collection for uploaded studio clips.

Removing an uploaded clip from its *last* set must GC the upload itself:
the proxy + AI-store bytes (via CacheActions), the poster (via
ThumbnailService), and the `uploaded_clip` row. A clip still referenced
by another set must be left untouched, and archive (non-uploaded) clips
are never GC'd.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.studio_sets import StudioSetsRepo
from backend.app.repositories.uploaded_clips import UploadedClipsRepo
from backend.app.services.upload_cleanup import UploadCleanup
from backend.app.uploaded_ids import to_clip_id


@pytest.fixture
async def db(tmp_path: Path):
    cm = open_db(tmp_path / "test.db")
    conn = await cm.__aenter__()
    await apply_migrations(conn, Path("backend/migrations"))
    yield conn
    await cm.__aexit__(None, None, None)


def _make_cleanup(db, *, thumbnail_service=None):
    cache_actions = MagicMock()
    cache_actions.evict_clip_everywhere = AsyncMock()
    cleanup = UploadCleanup(
        db_provider=lambda: db,
        studio_sets_repo=StudioSetsRepo(),
        uploaded_clips_repo=UploadedClipsRepo(),
        cache_actions=cache_actions,
        thumbnail_service=thumbnail_service,
    )
    return cleanup, cache_actions


async def _make_upload(db) -> int:
    repo = UploadedClipsRepo()
    pk = await repo.create(
        db, original_filename="lab.mp4", mime="video/mp4", size_bytes=10, ext=".mp4"
    )
    return to_clip_id(pk)


@pytest.mark.asyncio
async def test_gc_orphaned_upload_evicts_everything(db):
    sets = StudioSetsRepo()
    clip_id = await _make_upload(db)
    sid = await sets.create_set(db, name="Uploads", source="uploaded")
    await sets.add_clips(db, sid, clip_ids=[clip_id])
    # Simulate the membership removal the route does before GC.
    await sets.remove_clip(db, sid, clip_id=clip_id)

    thumb = MagicMock()
    thumb.evict = AsyncMock()
    cleanup, cache_actions = _make_cleanup(db, thumbnail_service=thumb)

    did_gc = await cleanup.gc_if_orphaned(clip_id)

    assert did_gc is True
    key = ("uploaded", str(clip_id))
    cache_actions.evict_clip_everywhere.assert_awaited_once()
    assert cache_actions.evict_clip_everywhere.call_args.args[0] == key
    thumb.evict.assert_awaited_once_with(clip_id)
    # uploaded_clip row is gone.
    assert await UploadedClipsRepo().get(db, clip_id) is None


@pytest.mark.asyncio
async def test_clip_still_in_another_set_is_untouched(db):
    sets = StudioSetsRepo()
    clip_id = await _make_upload(db)
    a = await sets.create_set(db, name="A", source="uploaded")
    b = await sets.create_set(db, name="B", source="uploaded")
    await sets.add_clips(db, a, clip_ids=[clip_id])
    await sets.add_clips(db, b, clip_ids=[clip_id])
    await sets.remove_clip(db, a, clip_id=clip_id)  # still in B

    thumb = MagicMock()
    thumb.evict = AsyncMock()
    cleanup, cache_actions = _make_cleanup(db, thumbnail_service=thumb)

    did_gc = await cleanup.gc_if_orphaned(clip_id)

    assert did_gc is False
    cache_actions.evict_clip_everywhere.assert_not_awaited()
    thumb.evict.assert_not_awaited()
    assert await UploadedClipsRepo().get(db, clip_id) is not None


@pytest.mark.asyncio
async def test_archive_clip_is_never_gced(db):
    cleanup, cache_actions = _make_cleanup(db)
    did_gc = await cleanup.gc_if_orphaned(12041)  # below UPLOAD_ID_BASE
    assert did_gc is False
    cache_actions.evict_clip_everywhere.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_thumbnail_service_still_gcs_row_and_bytes(db):
    sets = StudioSetsRepo()
    clip_id = await _make_upload(db)
    sid = await sets.create_set(db, name="Uploads", source="uploaded")
    await sets.add_clips(db, sid, clip_ids=[clip_id])
    await sets.remove_clip(db, sid, clip_id=clip_id)

    cleanup, cache_actions = _make_cleanup(db, thumbnail_service=None)

    did_gc = await cleanup.gc_if_orphaned(clip_id)

    assert did_gc is True
    cache_actions.evict_clip_everywhere.assert_awaited_once()
    assert await UploadedClipsRepo().get(db, clip_id) is None
