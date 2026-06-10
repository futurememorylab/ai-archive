"""Ingest route: store + index + thumbnail + set membership for uploads."""

import importlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.app.services.media_locator import LocalFile
from backend.app.uploaded_ids import is_uploaded
from tests._helpers.live_ctx import install_live_ctx


def _make_app(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("CATDV_PASSWORD", raising=False)
    from backend.app import main as main_mod
    importlib.reload(main_mod)
    return main_mod.app, tmp_path


@pytest.fixture
def ctx(monkeypatch, tmp_path):
    app, data_dir = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as c:
        yield c, data_dir


def test_upload_creates_clip_and_membership(ctx):
    client, data_dir = ctx

    # Wire a minimal media_cache_backend so /api/media/<id> can serve the
    # uploaded file from disk. The upload handler stores the file at
    # data_dir/cache/uploads/<clip_id>.mp4 before we GET the media route.
    async def _locate(clip_id):
        path = data_dir / "cache" / "uploads" / f"{clip_id}.mp4"
        return LocalFile(path) if path.exists() else None

    install_live_ctx(client.app, media_cache_backend=MagicMock(locate=_locate))

    r = client.post(
        "/api/studio/uploads",
        files={
            "file": ("My Clip.mp4", b"fake-mp4-bytes", "video/mp4"),
            "poster": ("p.jpg", b"jpeg-bytes", "image/jpeg"),
        },
        data={"duration_secs": "12.5"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    clip_id = body["clip_id"]
    set_id = body["set_id"]
    assert is_uploaded(clip_id)

    # File on disk + thumbnail stored.
    assert (data_dir / "cache" / "uploads" / f"{clip_id}.mp4").read_bytes() == b"fake-mp4-bytes"
    assert (data_dir / "cache" / "thumbs" / f"{clip_id}.jpg").read_bytes() == b"jpeg-bytes"

    # Membership in an uploaded set + landed in the default 'Uploads' set.
    clips = client.get(f"/api/studio/sets/{set_id}/clips").json()
    assert [c["clip_id"] for c in clips] == [clip_id]
    uploaded_sets = client.get("/api/studio/sets?source=uploaded").json()
    assert uploaded_sets[0]["name"] == "Uploads"

    # The /media route resolves the proxy from the pre-seeded cache.
    assert client.get(f"/api/media/{clip_id}").status_code == 200

    # The thumb also serves offline from the DB-first cache (poster pre-stored at ingest).
    assert client.get(f"/api/media/{clip_id}/thumb").status_code == 200


def test_upload_into_explicit_set(ctx):
    client, _ = ctx
    sid = client.post("/api/studio/sets?source=uploaded", json={"name": "B-roll"}).json()["id"]
    r = client.post(
        "/api/studio/uploads",
        files={"file": ("a.webm", b"x", "video/webm")},
        data={"set_id": str(sid)},
    )
    assert r.status_code == 201
    assert r.json()["set_id"] == sid


def test_rejects_non_web_safe_format(ctx):
    client, _ = ctx
    r = client.post(
        "/api/studio/uploads",
        files={"file": ("a.mov", b"x", "video/quicktime")},
    )
    assert r.status_code == 415


def test_hx_request_returns_card_partial(ctx):
    client, _ = ctx
    r = client.post(
        "/api/studio/uploads",
        files={"file": ("nice.mp4", b"x", "video/mp4")},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 201
    assert "studio-clip-card" in r.text
    assert "nice.mp4" in r.text


# ── ai_store mode: upload also pushes to GCS ─────────────────────────────────

def _make_app_ai_store(monkeypatch, tmp_path):
    """Boot the app with media_cache=ai_store; no CatDV credentials so
    live_ctx must be injected manually via install_live_ctx."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("MEDIA_CACHE", "ai_store")
    monkeypatch.delenv("CATDV_PASSWORD", raising=False)
    from backend.app import main as main_mod
    importlib.reload(main_mod)
    return main_mod.app, tmp_path


def test_upload_in_ai_store_mode_calls_ensure_uploaded(monkeypatch, tmp_path):
    """With media_cache=ai_store, the handler must call ai_store.ensure_uploaded
    with key ("uploaded", <clip_id>) and the on-disk dest path."""
    app, data_dir = _make_app_ai_store(monkeypatch, tmp_path)

    fake_ai_store = MagicMock()
    fake_ai_store.ensure_uploaded = AsyncMock(return_value=MagicMock())

    with TestClient(app) as client:
        install_live_ctx(client.app, ai_store=fake_ai_store)

        r = client.post(
            "/api/studio/uploads",
            files={"file": ("clip.mp4", b"mp4-content", "video/mp4")},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        clip_id = body["clip_id"]

    # ensure_uploaded called exactly once
    fake_ai_store.ensure_uploaded.assert_called_once()
    call_args = fake_ai_store.ensure_uploaded.call_args

    # key must be ("uploaded", str(clip_id))
    key = call_args.args[0] if call_args.args else call_args.kwargs.get("clip_key")
    assert key == ("uploaded", str(clip_id)), f"Expected ('uploaded', '{clip_id}'), got {key!r}"

    # path must be the on-disk uploads dest
    expected_dest = data_dir / "cache" / "uploads" / f"{clip_id}.mp4"
    path_arg = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("local_path")
    assert Path(path_arg) == expected_dest, f"Expected dest {expected_dest}, got {path_arg!r}"

    # mime must be video/mp4
    mime_arg = call_args.args[2] if len(call_args.args) > 2 else call_args.kwargs.get("mime")
    assert mime_arg == "video/mp4", f"Expected mime video/mp4, got {mime_arg!r}"


def test_upload_in_local_mode_does_not_call_ensure_uploaded(monkeypatch, tmp_path):
    """With default media_cache=local, ensure_uploaded must NOT be called."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("MEDIA_CACHE", raising=False)
    monkeypatch.delenv("CATDV_PASSWORD", raising=False)
    from backend.app import main as main_mod
    importlib.reload(main_mod)
    app = main_mod.app

    fake_ai_store = MagicMock()
    fake_ai_store.ensure_uploaded = AsyncMock(return_value=MagicMock())

    with TestClient(app) as client:
        install_live_ctx(client.app, ai_store=fake_ai_store)

        r = client.post(
            "/api/studio/uploads",
            files={"file": ("clip.mp4", b"mp4-content", "video/mp4")},
        )
        assert r.status_code == 201, r.text

    fake_ai_store.ensure_uploaded.assert_not_called()
