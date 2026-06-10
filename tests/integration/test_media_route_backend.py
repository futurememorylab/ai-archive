"""stream_media must resolve playback via MediaCacheBackend.locate().

Tests drive the REAL handler and inject a fake backend via
app.state.live_ctx (the same mechanism used by install_live_ctx).
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.routes.media import router
from backend.app.services.media_locator import LocalFile, RemoteUrl


class _Backend:
    def __init__(self, located):
        self._located = located

    async def locate(self, clip_id):
        return self._located


def _client(located, tmp_path=None):
    app = FastAPI()
    app.include_router(router)
    # LiveCtx is accessed via request.app.state.live_ctx (not FastAPI Depends),
    # so we stash a stub directly on app.state.
    live = type("FakeLive", (), {"media_cache_backend": _Backend(located)})()
    app.state.live_ctx = live
    app.state.core_ctx = None  # thumb handler uses core_ctx; not under test here
    return TestClient(app)


def _client_no_backend():
    app = FastAPI()
    app.include_router(router)
    live = type("FakeLive", (), {"media_cache_backend": None})()
    app.state.live_ctx = live
    app.state.core_ctx = None
    return TestClient(app)


def test_remote_url_returns_307():
    c = _client(RemoteUrl("https://storage.googleapis.com/x"))
    r = c.get("/api/media/5", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "https://storage.googleapis.com/x"


def test_miss_returns_404():
    c = _client(None)
    r = c.get("/api/media/5")
    assert r.status_code == 404


def test_local_file_serves_bytes(tmp_path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"\x00" * 64)
    c = _client(LocalFile(f))
    r = c.get("/api/media/5")
    assert r.status_code == 200
    assert r.headers["accept-ranges"] == "bytes"


def test_local_file_range_request(tmp_path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(bytes(range(100)))
    c = _client(LocalFile(f))
    r = c.get("/api/media/5", headers={"Range": "bytes=10-19"})
    assert r.status_code == 206
    assert r.content == bytes(range(10, 20))


def test_backend_none_returns_503():
    c = _client_no_backend()
    r = c.get("/api/media/5")
    assert r.status_code == 503
