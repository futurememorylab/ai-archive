"""stream_media must serve LocalFile via the existing file path and
RemoteUrl via 307 redirect (browser range requests then hit GCS
directly), and turn MediaNotAvailable into the usual 404."""

from pathlib import Path

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.app.routes.media import router
from backend.app.services.media_locator import (
    LocalFile,
    MediaNotAvailable,
    RemoteUrl,
)


class StubLocator:
    def __init__(self, result):
        self._result = result

    async def locate(self, clip_id):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class StubLive:
    def __init__(self, locator):
        self.media_locator = locator


def make_app(locator):
    app = FastAPI()
    app.include_router(router)
    app.state.live_ctx = StubLive(locator)
    app.state.core_ctx = None  # not consulted for non-uploaded clip ids
    return app


async def _get(app, path, **kwargs):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.get(path, **kwargs)


async def test_remote_url_redirects_307():
    app = make_app(StubLocator(RemoteUrl("https://storage.googleapis.com/b/c?sig=1")))
    resp = await _get(app, "/api/media/123", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"].startswith("https://storage.googleapis.com/")


async def test_local_file_serves_bytes(tmp_path: Path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"\x00" * 64)
    app = make_app(StubLocator(LocalFile(f)))
    resp = await _get(app, "/api/media/123")
    assert resp.status_code == 200
    assert resp.headers["accept-ranges"] == "bytes"


async def test_local_file_range_request(tmp_path: Path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(bytes(range(100)))
    app = make_app(StubLocator(LocalFile(f)))
    resp = await _get(app, "/api/media/123", headers={"Range": "bytes=10-19"})
    assert resp.status_code == 206
    assert resp.content == bytes(range(10, 20))


async def test_miss_is_404():
    app = make_app(StubLocator(MediaNotAvailable(123, "local cache: x; ai store: y")))
    resp = await _get(app, "/api/media/123")
    assert resp.status_code == 404
    assert "local cache" in resp.text
