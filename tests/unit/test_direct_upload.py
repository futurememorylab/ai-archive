"""DirectUpload: the browser PUTs a Studio upload straight into the bucket
(Cloud Run refuses requests over 32 MB); the app signs the URL, then checks
the blob and registers it where playback and annotation look for it."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.services.direct_upload import (
    UPLOAD_URL_TTL_S,
    DirectUpload,
    UploadNotReceived,
    build_direct_upload,
)
from backend.app.services.gcs import BlobMeta

CLIP = 1_000_000_007


def _direct(meta=None):
    gcs = MagicMock()
    gcs.signed_upload_url.return_value = "https://signed.example/put"
    gcs.blob_meta.return_value = meta
    gcs.gs_uri.return_value = f"gs://bucket/instances/prod/uploads/{CLIP}.mov"
    repo = MagicMock(upsert=AsyncMock())
    db = object()
    return (
        DirectUpload(gcs=gcs, files_repo=repo, store_id="gcs:bucket", db_provider=lambda: db),
        gcs,
        repo,
        db,
    )


async def test_upload_url_signs_a_put_for_the_clip():
    direct, gcs, _, _ = _direct()
    assert await direct.upload_url(CLIP, "video/mp4") == "https://signed.example/put"
    gcs.signed_upload_url.assert_called_once_with(CLIP, "video/mp4", expires_s=UPLOAD_URL_TTL_S)


async def test_complete_registers_the_blob_as_the_ai_store_copy():
    direct, _, repo, db = _direct(BlobMeta(size=50_000_000, content_type="video/mp4", md5="abc"))
    await direct.complete(CLIP, mime="video/mp4", size_bytes=50_000_000)

    repo.upsert.assert_awaited_once()
    args, kwargs = repo.upsert.call_args
    assert args == (db,)
    assert kwargs["store_id"] == "gcs:bucket"
    assert kwargs["clip_id"] == CLIP
    assert kwargs["gcs_uri"] == f"gs://bucket/instances/prod/uploads/{CLIP}.mov"
    assert kwargs["mime_type"] == "video/mp4"
    assert kwargs["size_bytes"] == 50_000_000
    assert kwargs["provider_id"] == "uploaded"


async def test_complete_refuses_a_missing_blob():
    direct, _, repo, _ = _direct(None)
    with pytest.raises(UploadNotReceived, match="never reached"):
        await direct.complete(CLIP, mime="video/mp4", size_bytes=10)
    repo.upsert.assert_not_awaited()


async def test_complete_refuses_a_blob_of_another_size():
    # The size cap was checked against the announced size, so a different
    # (e.g. bigger) blob must not be accepted.
    direct, _, repo, _ = _direct(BlobMeta(size=999, content_type="video/mp4", md5=None))
    with pytest.raises(UploadNotReceived, match="999 bytes"):
        await direct.complete(CLIP, mime="video/mp4", size_bytes=10)
    repo.upsert.assert_not_awaited()


async def test_complete_refuses_a_blob_of_another_type():
    direct, _, repo, _ = _direct(BlobMeta(size=10, content_type="text/html", md5=None))
    with pytest.raises(UploadNotReceived, match="text/html"):
        await direct.complete(CLIP, mime="video/mp4", size_bytes=10)
    repo.upsert.assert_not_awaited()


async def test_discard_deletes_the_blob():
    direct, gcs, _, _ = _direct()
    await direct.discard(CLIP)
    gcs.delete.assert_called_once_with(CLIP)


@pytest.mark.parametrize(
    ("media_cache", "store_id", "built"),
    [
        ("ai_store", "gcs:bucket", True),
        ("local", "gcs:bucket", False),
        ("ai_store", "gemini-files", False),
    ],
)
def test_built_only_for_a_gcs_ai_store_in_cloud_mode(media_cache, store_id, built):
    out = build_direct_upload(
        SimpleNamespace(media_cache=media_cache),
        gcs=MagicMock(),
        ai_store=SimpleNamespace(id=store_id),
        files_repo=MagicMock(),
        db_provider=lambda: None,
    )
    assert (out is not None) is built
