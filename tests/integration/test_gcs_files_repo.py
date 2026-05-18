import pytest

from backend.app.repositories.gcs_files import GcsFilesRepo


@pytest.mark.asyncio
async def test_upsert_and_get(db):
    repo = GcsFilesRepo()
    await repo.upsert(
        db,
        clip_id=42,
        gcs_uri="gs://b/clips/42.mov",
        mime_type="video/quicktime",
        size_bytes=100,
        sha256="abc",
    )
    row = await repo.get(db, 42)
    assert row is not None
    assert row["gcs_uri"] == "gs://b/clips/42.mov"


@pytest.mark.asyncio
async def test_upsert_replaces_on_new_sha(db):
    repo = GcsFilesRepo()
    await repo.upsert(
        db,
        clip_id=42,
        gcs_uri="gs://b/clips/42.mov",
        mime_type="video/quicktime",
        size_bytes=100,
        sha256="aaa",
    )
    await repo.upsert(
        db,
        clip_id=42,
        gcs_uri="gs://b/clips/42.mov",
        mime_type="video/quicktime",
        size_bytes=200,
        sha256="bbb",
    )
    row = await repo.get(db, 42)
    assert row["sha256"] == "bbb"
    assert row["size_bytes"] == 200
