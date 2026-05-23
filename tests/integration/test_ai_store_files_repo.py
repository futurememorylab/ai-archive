import pytest

from backend.app.repositories.ai_store_files import AIStoreFilesRepo


@pytest.mark.asyncio
async def test_upsert_and_get(db):
    repo = AIStoreFilesRepo()
    await repo.upsert(
        db,
        store_id="gcs:b",
        clip_id=42,
        gcs_uri="gs://b/clips/42.mov",
        mime_type="video/quicktime",
        size_bytes=100,
        sha256="abc",
    )
    row = await repo.get(db, store_id="gcs:b", clip_id=42)
    assert row is not None
    assert row["gcs_uri"] == "gs://b/clips/42.mov"
    assert row["store_id"] == "gcs:b"
    assert row["sha256"] == "abc"


@pytest.mark.asyncio
async def test_get_returns_none_when_store_mismatch(db):
    repo = AIStoreFilesRepo()
    await repo.upsert(
        db,
        store_id="gcs:a",
        clip_id=42,
        gcs_uri="gs://a/clips/42.mov",
        mime_type="video/quicktime",
        size_bytes=100,
        sha256="x",
    )
    assert await repo.get(db, store_id="gcs:other", clip_id=42) is None


@pytest.mark.asyncio
async def test_upsert_replaces_on_same_pk(db):
    repo = AIStoreFilesRepo()
    await repo.upsert(
        db,
        store_id="gcs:b",
        clip_id=42,
        gcs_uri="gs://b/clips/42.mov",
        mime_type="video/quicktime",
        size_bytes=100,
        sha256="aaa",
    )
    await repo.upsert(
        db,
        store_id="gcs:b",
        clip_id=42,
        gcs_uri="gs://b/clips/42.mov",
        mime_type="video/quicktime",
        size_bytes=200,
        sha256="bbb",
    )
    row = await repo.get(db, store_id="gcs:b", clip_id=42)
    assert row["sha256"] == "bbb"
    assert row["size_bytes"] == 200


@pytest.mark.asyncio
async def test_two_stores_can_hold_same_clip_independently(db):
    repo = AIStoreFilesRepo()
    await repo.upsert(
        db,
        store_id="gcs:a",
        clip_id=42,
        gcs_uri="gs://a/clips/42.mov",
        mime_type="video/quicktime",
        size_bytes=100,
        sha256="aa",
    )
    await repo.upsert(
        db,
        store_id="gcs:b",
        clip_id=42,
        gcs_uri="gs://b/clips/42.mov",
        mime_type="video/quicktime",
        size_bytes=100,
        sha256="bb",
    )
    a = await repo.get(db, store_id="gcs:a", clip_id=42)
    b = await repo.get(db, store_id="gcs:b", clip_id=42)
    assert a["sha256"] == "aa"
    assert b["sha256"] == "bb"


@pytest.mark.asyncio
async def test_touch_updates_last_used_at(db):
    repo = AIStoreFilesRepo()
    await repo.upsert(
        db,
        store_id="gcs:b",
        clip_id=42,
        gcs_uri="gs://b/clips/42.mov",
        mime_type="video/quicktime",
        size_bytes=100,
        sha256="abc",
    )
    # Force a different timestamp by writing it directly. (We don't sleep.)
    await db.execute(
        "UPDATE ai_store_files SET last_used_at = ? WHERE store_id = ? AND catdv_clip_id = ?",
        ("2020-01-01T00:00:00+00:00", "gcs:b", 42),
    )
    await db.commit()

    await repo.touch(db, store_id="gcs:b", clip_id=42)
    after = (await repo.get(db, store_id="gcs:b", clip_id=42))["last_used_at"]
    assert after > "2020-01-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_delete_row(db):
    repo = AIStoreFilesRepo()
    await repo.upsert(
        db,
        store_id="gcs:b",
        clip_id=42,
        gcs_uri="gs://b/clips/42.mov",
        mime_type="video/quicktime",
        size_bytes=100,
        sha256="abc",
    )
    await repo.delete(db, store_id="gcs:b", clip_id=42)
    assert await repo.get(db, store_id="gcs:b", clip_id=42) is None
