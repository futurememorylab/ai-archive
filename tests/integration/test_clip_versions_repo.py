import pytest

from backend.app.models.annotation import ClipVersion
from backend.app.repositories.clip_versions import ClipVersionsRepo


def _v(clip_id=1, num=1, state="publishing", origin="publish"):
    return ClipVersion(
        catdv_clip_id=clip_id,
        version_num=num,
        snapshot={"markers": [], "fields": {}, "notes": None},
        origin=origin,
        publish_state=state,
    )


@pytest.mark.asyncio
async def test_insert_and_get_roundtrip(db):
    repo = ClipVersionsRepo()
    vid = await repo.insert(db, _v())
    got = await repo.get(db, vid)
    assert got.id == vid
    assert got.catdv_clip_id == 1
    assert got.publish_state == "publishing"
    assert got.snapshot == {"markers": [], "fields": {}, "notes": None}


@pytest.mark.asyncio
async def test_next_version_num_is_per_clip_max_plus_one(db):
    repo = ClipVersionsRepo()
    assert await repo.next_version_num(db, 1) == 1
    await repo.insert(db, _v(clip_id=1, num=1))
    await repo.insert(db, _v(clip_id=1, num=2))
    await repo.insert(db, _v(clip_id=2, num=1))
    assert await repo.next_version_num(db, 1) == 3
    assert await repo.next_version_num(db, 2) == 2


@pytest.mark.asyncio
async def test_list_by_clip_newest_first(db):
    repo = ClipVersionsRepo()
    await repo.insert(db, _v(num=1))
    await repo.insert(db, _v(num=2))
    rows = await repo.list_by_clip(db, 1)
    assert [r.version_num for r in rows] == [2, 1]


@pytest.mark.asyncio
async def test_mark_live_supersedes_prior_live(db):
    repo = ClipVersionsRepo()
    v1 = await repo.insert(db, _v(num=1, state="live"))
    v2 = await repo.insert(db, _v(num=2, state="publishing"))
    await repo.mark_live(db, v2)
    assert (await repo.get(db, v2)).publish_state == "live"
    assert (await repo.get(db, v1)).publish_state == "superseded"
    assert (await repo.get(db, v2)).synced_at is not None


@pytest.mark.asyncio
async def test_mark_failed_leaves_prior_live(db):
    repo = ClipVersionsRepo()
    v1 = await repo.insert(db, _v(num=1, state="live"))
    v2 = await repo.insert(db, _v(num=2, state="publishing"))
    await repo.mark_failed(db, v2, reason="boom")
    assert (await repo.get(db, v2)).publish_state == "failed"
    assert (await repo.get(db, v1)).publish_state == "live"


@pytest.mark.asyncio
async def test_mark_live_supersedes_orphaned_publishing_siblings(db):
    """mark_live cleans up other 'publishing' rows for the clip so a merged
    multi-publish (or a stuck pile-up) doesn't orphan them. Audit A4."""
    repo = ClipVersionsRepo()
    v1 = await repo.insert(db, _v(num=1, state="live"))
    v2 = await repo.insert(db, _v(num=2, state="publishing"))  # orphaned
    v3 = await repo.insert(db, _v(num=3, state="publishing"))  # the winner
    await repo.mark_live(db, v3)
    assert (await repo.get(db, v3)).publish_state == "live"
    assert (await repo.get(db, v1)).publish_state == "superseded"
    assert (await repo.get(db, v2)).publish_state == "superseded"


@pytest.mark.asyncio
async def test_mark_publishing_moves_back_to_publishing(db):
    repo = ClipVersionsRepo()
    v = await repo.insert(db, _v(num=1, state="superseded"))
    await repo.mark_publishing(db, v)
    assert (await repo.get(db, v)).publish_state == "publishing"
