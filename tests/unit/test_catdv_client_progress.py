"""Issue #78: the download path reports absolute bytes-on-disk + total
through an optional progress callback, per chunk."""

import pytest


class _FakeStreamResp:
    """Minimal stand-in for httpx streaming response over _stream_to_file."""

    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks

    async def aiter_bytes(self, chunk_size):
        for c in self._chunks:
            yield c


def _client():
    # _stream_to_file uses only self via the method; construct without network.
    from backend.app.services.catdv_client import CatdvClient
    return CatdvClient.__new__(CatdvClient)


@pytest.mark.asyncio
async def test_stream_reports_absolute_progress_with_base(tmp_path):
    client = _client()
    dest = tmp_path / "clip.mov"
    seen: list[tuple[int, int]] = []

    async def cb(downloaded: int, total: int) -> None:
        seen.append((downloaded, total))

    resp = _FakeStreamResp([b"a" * 10, b"b" * 5])
    await client._stream_to_file(
        resp, dest, append=False, chunk_size=1024,
        progress_cb=cb, base=100, total=200,
    )
    # absolute = base(100) + cumulative written
    assert seen == [(110, 200), (115, 200)]


@pytest.mark.asyncio
async def test_stream_no_callback_is_noop(tmp_path):
    client = _client()
    dest = tmp_path / "clip.mov"
    resp = _FakeStreamResp([b"x" * 3])
    # No progress_cb passed → must not raise, file still written.
    await client._stream_to_file(resp, dest, append=False, chunk_size=1024)
    assert dest.read_bytes() == b"x" * 3
