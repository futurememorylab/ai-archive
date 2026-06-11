import importlib
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from tests._helpers.live_ctx import install_live_ctx


def _make_app_ai_store(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("MEDIA_CACHE", "ai_store")
    monkeypatch.delenv("CATDV_PASSWORD", raising=False)
    from backend.app import main as main_mod
    importlib.reload(main_mod)
    return main_mod.app, tmp_path


def test_uploaded_thumb_served_from_durable_after_restart(monkeypatch, tmp_path):
    # Simulates a restarted instance: /data/cache/thumbs is empty, but the
    # poster is in GCS. The route must fall through to the durable store.
    app, data_dir = _make_app_ai_store(monkeypatch, tmp_path)
    from backend.app.uploaded_ids import to_clip_id
    cid = to_clip_id(5)
    poster_file = data_dir / "cache" / "thumbs" / f"{cid}.jpg"

    async def _fake_get_or_fetch(clip_id):
        poster_file.parent.mkdir(parents=True, exist_ok=True)
        poster_file.write_bytes(b"\xff\xd8GCS")
        return poster_file

    fake_thumb = MagicMock()
    fake_thumb.get_or_fetch = AsyncMock(side_effect=_fake_get_or_fetch)

    with TestClient(app) as client:
        install_live_ctx(client.app, thumbnail_service=fake_thumb)
        r = client.get(f"/api/media/{cid}/thumb")

    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/jpeg"
    assert r.content == b"\xff\xd8GCS"
    fake_thumb.get_or_fetch.assert_called_once_with(cid)


def test_uploaded_thumb_404_when_durable_misses(monkeypatch, tmp_path):
    app, data_dir = _make_app_ai_store(monkeypatch, tmp_path)
    from backend.app.uploaded_ids import to_clip_id
    cid = to_clip_id(6)

    fake_thumb = MagicMock()
    fake_thumb.get_or_fetch = AsyncMock(return_value=None)

    with TestClient(app) as client:
        install_live_ctx(client.app, thumbnail_service=fake_thumb)
        r = client.get(f"/api/media/{cid}/thumb")

    assert r.status_code == 404
