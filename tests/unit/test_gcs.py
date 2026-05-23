from pathlib import Path
from unittest.mock import MagicMock

from backend.app.services.gcs import GcsService


def _wire_mock_bucket(blob_exists: bool):
    bucket = MagicMock(name="bucket")
    bucket.name = "test-bucket"
    blob = MagicMock(name="blob")
    blob.exists.return_value = blob_exists
    bucket.blob.return_value = blob
    return bucket, blob


def test_upload_if_absent_uploads_when_missing(tmp_path: Path):
    local = tmp_path / "f.mov"
    local.write_bytes(b"data")

    bucket, blob = _wire_mock_bucket(blob_exists=False)
    service = GcsService.__new__(GcsService)
    service._bucket = bucket

    uri = service.upload_if_absent(clip_id=42, local_path=local, mime="video/quicktime")
    blob.upload_from_filename.assert_called_once_with(
        str(local), content_type="video/quicktime", timeout=1800
    )
    assert uri == "gs://test-bucket/clips/42.mov"
    bucket.blob.assert_called_with("clips/42.mov", chunk_size=8 * 1024 * 1024)


def test_upload_if_absent_skips_when_present(tmp_path: Path):
    local = tmp_path / "f.mov"
    local.write_bytes(b"data")

    bucket, blob = _wire_mock_bucket(blob_exists=True)
    service = GcsService.__new__(GcsService)
    service._bucket = bucket

    uri = service.upload_if_absent(clip_id=42, local_path=local, mime="video/quicktime")
    blob.upload_from_filename.assert_not_called()
    assert uri == "gs://test-bucket/clips/42.mov"


def test_delete_calls_blob_delete():
    bucket, blob = _wire_mock_bucket(blob_exists=True)
    service = GcsService.__new__(GcsService)
    service._bucket = bucket

    service.delete(clip_id=42)
    blob.delete.assert_called_once()
