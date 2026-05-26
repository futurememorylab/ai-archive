from io import BytesIO

import pytest

from backend.app.services.studio_uploads import UploadError, save_upload


class _UploadFile:
    """Mimics fastapi.UploadFile enough for the service."""
    def __init__(self, *, filename, content_type, data):
        self.filename = filename
        self.content_type = content_type
        self._buf = BytesIO(data)

    async def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)


@pytest.mark.asyncio
async def test_save_upload_writes_file_and_returns_relative_path(tmp_path):
    up = _UploadFile(filename="cool.MP4", content_type="video/mp4", data=b"\x00" * 16)
    path = await save_upload(up, uploads_dir=tmp_path, max_mb=10)
    full = tmp_path / path
    assert full.exists()
    assert full.suffix == ".mp4"
    assert full.read_bytes() == b"\x00" * 16


@pytest.mark.asyncio
async def test_save_upload_rejects_non_video_mime(tmp_path):
    up = _UploadFile(filename="x.exe", content_type="application/x-exe", data=b"")
    with pytest.raises(UploadError, match="video"):
        await save_upload(up, uploads_dir=tmp_path, max_mb=10)


@pytest.mark.asyncio
async def test_save_upload_rejects_over_size(tmp_path):
    big = b"\x00" * (2 * 1024 * 1024 + 1)
    up = _UploadFile(filename="x.mp4", content_type="video/mp4", data=big)
    with pytest.raises(UploadError, match="exceeds"):
        await save_upload(up, uploads_dir=tmp_path, max_mb=2)
