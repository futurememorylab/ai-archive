import base64
import hashlib
from pathlib import Path
from unittest.mock import MagicMock

from backend.app.services.gcs import GcsService


def _md5_b64(data: bytes) -> str:
    return base64.b64encode(hashlib.md5(data).digest()).decode()


def _wire_mock_bucket(*, existing_md5: str | None):
    """Wire a fake bucket. `existing_md5` None means the blob is absent;
    otherwise it is the md5_hash GCS reports for the already-stored object."""
    bucket = MagicMock(name="bucket")
    bucket.name = "test-bucket"
    blob = MagicMock(name="blob")
    bucket.blob.return_value = blob
    if existing_md5 is None:
        bucket.get_blob.return_value = None
    else:
        existing = MagicMock(name="existing_blob")
        existing.md5_hash = existing_md5
        bucket.get_blob.return_value = existing
    return bucket, blob


def test_upload_if_absent_uploads_when_missing(tmp_path: Path):
    local = tmp_path / "f.mov"
    local.write_bytes(b"data")

    bucket, blob = _wire_mock_bucket(existing_md5=None)
    service = GcsService.__new__(GcsService)
    service._bucket = bucket

    uri = service.upload_if_absent(clip_id=42, local_path=local, mime="video/quicktime")
    blob.upload_from_filename.assert_called_once_with(
        str(local), content_type="video/quicktime", timeout=1800
    )
    assert uri == "gs://test-bucket/clips/42.mov"
    bucket.blob.assert_called_with("clips/42.mov", chunk_size=8 * 1024 * 1024)


def test_upload_if_absent_skips_when_present_with_matching_content(tmp_path: Path):
    local = tmp_path / "f.mov"
    local.write_bytes(b"data")

    bucket, blob = _wire_mock_bucket(existing_md5=_md5_b64(b"data"))
    service = GcsService.__new__(GcsService)
    service._bucket = bucket

    uri = service.upload_if_absent(clip_id=42, local_path=local, mime="video/quicktime")
    blob.upload_from_filename.assert_not_called()
    assert uri == "gs://test-bucket/clips/42.mov"


def test_upload_if_absent_reuploads_when_content_differs(tmp_path: Path):
    # An orphan/stale blob with the same name but different bytes must be
    # overwritten -- otherwise a reused clip_id silently serves stale media.
    local = tmp_path / "f.mov"
    local.write_bytes(b"new-bytes")

    bucket, blob = _wire_mock_bucket(existing_md5=_md5_b64(b"OLD-STALE-BYTES"))
    service = GcsService.__new__(GcsService)
    service._bucket = bucket

    uri = service.upload_if_absent(clip_id=42, local_path=local, mime="video/quicktime")
    blob.upload_from_filename.assert_called_once_with(
        str(local), content_type="video/quicktime", timeout=1800
    )
    assert uri == "gs://test-bucket/clips/42.mov"


def test_delete_calls_blob_delete():
    bucket, blob = _wire_mock_bucket(existing_md5="x")
    service = GcsService.__new__(GcsService)
    service._bucket = bucket

    service.delete(clip_id=42)
    blob.delete.assert_called_once()


def test_thumb_uri_path():
    bucket = MagicMock(); bucket.name = "test-bucket"
    service = GcsService.__new__(GcsService); service._bucket = bucket
    assert service.thumb_uri(7) == "gs://test-bucket/thumbs/7.jpg"


def test_download_thumb_returns_false_when_absent(tmp_path: Path):
    bucket = MagicMock(); bucket.name = "test-bucket"
    bucket.get_blob.return_value = None
    service = GcsService.__new__(GcsService); service._bucket = bucket
    assert service.download_thumb(7, tmp_path / "7.jpg") is False


def test_download_thumb_writes_and_returns_true(tmp_path: Path):
    bucket = MagicMock(); bucket.name = "test-bucket"
    blob = MagicMock()
    blob.download_to_filename.side_effect = lambda p, **k: Path(p).write_bytes(b"\xff\xd8jpg")
    bucket.get_blob.return_value = blob
    service = GcsService.__new__(GcsService); service._bucket = bucket
    dest = tmp_path / "7.jpg"
    assert service.download_thumb(7, dest) is True
    assert dest.read_bytes() == b"\xff\xd8jpg"
    bucket.get_blob.assert_called_with("thumbs/7.jpg")


def test_download_thumb_false_on_empty_body(tmp_path: Path):
    bucket = MagicMock(); bucket.name = "test-bucket"
    blob = MagicMock()
    blob.download_to_filename.side_effect = lambda p, **k: Path(p).write_bytes(b"")
    bucket.get_blob.return_value = blob
    service = GcsService.__new__(GcsService); service._bucket = bucket
    dest = tmp_path / "7.jpg"
    assert service.download_thumb(7, dest) is False
    assert not dest.exists()


def test_upload_thumb_overwrites_unconditionally(tmp_path: Path):
    local = tmp_path / "7.jpg"; local.write_bytes(b"jpg")
    bucket = MagicMock(); bucket.name = "test-bucket"
    blob = MagicMock(); bucket.blob.return_value = blob
    service = GcsService.__new__(GcsService); service._bucket = bucket
    uri = service.upload_thumb(7, local)
    blob.upload_from_filename.assert_called_once_with(str(local), content_type="image/jpeg")
    assert uri == "gs://test-bucket/thumbs/7.jpg"
    bucket.blob.assert_called_with("thumbs/7.jpg")
