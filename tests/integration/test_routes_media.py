import importlib
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from backend.app.services.media_locator import LocalFile
from tests._helpers.live_ctx import install_live_ctx


def _setenv(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))


def _app(monkeypatch, tmp_path):
    _setenv(monkeypatch, tmp_path)
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    return main_mod.app


def test_media_streams_full_file(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        proxy = tmp_path / "42.mov"
        proxy.write_bytes(b"V" * 1000)

        async def locate(clip_id):
            assert clip_id == 42
            return LocalFile(proxy)

        install_live_ctx(
            client.app,
            media_cache_backend=MagicMock(locate=locate),
        )

        r = client.get("/api/media/42")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("video/")
        assert r.content == b"V" * 1000


def test_media_serves_range(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        proxy = tmp_path / "42.mov"
        proxy.write_bytes(b"X" * 100 + b"Y" * 100)

        async def locate(clip_id):
            return LocalFile(proxy)

        install_live_ctx(
            client.app,
            media_cache_backend=MagicMock(locate=locate),
        )

        r = client.get("/api/media/42", headers={"Range": "bytes=100-199"})
        assert r.status_code == 206
        assert r.content == b"Y" * 100
        assert r.headers["content-range"] == "bytes 100-199/200"


def test_thumb_serves_jpeg(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        thumb = tmp_path / "42.jpg"
        thumb.write_bytes(b"\xff\xd8\xffJPEG")

        async def get_or_fetch(clip_id):
            assert clip_id == 42
            return thumb

        install_live_ctx(
            client.app,
            thumbnail_service=MagicMock(get_or_fetch=get_or_fetch),
        )

        r = client.get("/api/media/42/thumb")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/jpeg"
        assert r.content == b"\xff\xd8\xffJPEG"


def test_thumb_404_when_unavailable(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        async def get_or_fetch(clip_id):
            return None

        install_live_ctx(
            client.app,
            thumbnail_service=MagicMock(get_or_fetch=get_or_fetch),
        )

        r = client.get("/api/media/42/thumb")
        assert r.status_code == 404


def test_thumb_404_is_short_lived_cacheable(monkeypatch, tmp_path):
    """A 404 must carry a short browser cache so repeat page renders don't
    re-fire the same request storm against the thumb endpoint when CatDV is
    offline and the file isn't local."""
    app = _app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        async def get_or_fetch(clip_id):
            return None

        install_live_ctx(
            client.app,
            thumbnail_service=MagicMock(get_or_fetch=get_or_fetch),
        )

        r = client.get("/api/media/42/thumb")
        assert r.status_code == 404
        cc = r.headers.get("cache-control", "")
        assert "max-age=" in cc, f"missing max-age in {cc!r}"
        # Short lived: must not be longer than 10 minutes — the file may
        # arrive shortly via a prefetch or a CatDV reconnect.
        max_age = int(cc.split("max-age=")[1].split(",")[0].strip())
        assert 0 < max_age <= 600, f"max-age out of bounds: {max_age}"
