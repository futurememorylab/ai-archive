"""Ingest route: store + index + thumbnail + set membership for uploads."""

import importlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.app.services.media_locator import LocalFile
from backend.app.services.thumbnail_service import ThumbnailService
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


# ── orphan GC: removing an upload from its last set cleans it up ─────────────


def _thumb_service(data_dir):
    """Real ThumbnailService over the thumbs cache dir (no durable store),
    so evict() actually unlinks the local poster."""
    return ThumbnailService(
        cache_dir=data_dir / "cache" / "thumbs", archive=MagicMock()
    )


def test_remove_last_set_membership_gcs_the_upload(ctx):
    client, data_dir = ctx
    install_live_ctx(client.app, thumbnail_service=_thumb_service(data_dir))

    r = client.post(
        "/api/studio/uploads",
        files={
            "file": ("lab.mp4", b"fake-mp4-bytes", "video/mp4"),
            "poster": ("p.jpg", b"jpeg-bytes", "image/jpeg"),
        },
    )
    assert r.status_code == 201, r.text
    clip_id = r.json()["clip_id"]
    set_id = r.json()["set_id"]

    video = data_dir / "cache" / "uploads" / f"{clip_id}.mp4"
    thumb = data_dir / "cache" / "thumbs" / f"{clip_id}.jpg"
    assert video.exists() and thumb.exists()

    # Remove from its only set → GC.
    del_resp = client.delete(f"/api/studio/sets/{set_id}/clips/{clip_id}")
    assert del_resp.status_code == 204

    # DB row, local video, local thumb, proxy_cache row all gone.
    assert not video.exists()
    assert not thumb.exists()
    core = client.app.state.core_ctx
    assert client.portal.call(core.uploaded_clips_repo.get, core.db, clip_id) is None
    assert client.portal.call(core.proxy_cache_repo.get, core.db, clip_id) is None


def test_remove_from_one_of_two_sets_keeps_upload(ctx):
    client, data_dir = ctx
    install_live_ctx(client.app, thumbnail_service=_thumb_service(data_dir))

    r = client.post(
        "/api/studio/uploads",
        files={"file": ("keep.mp4", b"keep-bytes", "video/mp4")},
    )
    clip_id = r.json()["clip_id"]
    first_set = r.json()["set_id"]
    # Add the same clip to a second set.
    second = client.post(
        "/api/studio/sets?source=uploaded", json={"name": "second"}
    ).json()["id"]
    assert (
        client.post(
            f"/api/studio/sets/{second}/clips", json={"clip_ids": [clip_id]}
        ).status_code
        == 200
    )

    # Remove from the first set → still referenced by `second`, so no GC.
    del_resp = client.delete(f"/api/studio/sets/{first_set}/clips/{clip_id}")
    assert del_resp.status_code == 204

    video = data_dir / "cache" / "uploads" / f"{clip_id}.mp4"
    assert video.exists()
    core = client.app.state.core_ctx
    assert client.portal.call(core.uploaded_clips_repo.get, core.db, clip_id) is not None


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


def test_upload_in_local_mode_also_pushes_to_ai_store(monkeypatch, tmp_path):
    """Uploads must ALWAYS land in the AI store when one is available — even in
    the default media_cache=local mode. The local proxy_cache row is not a
    durable home (a DB reset / LRU eviction orphans the on-disk file, so
    annotation can no longer find bytes that are still on disk). Pushing to the
    AI store gives every upload a durable home the annotator's status()
    fast-path resolves regardless of local cache state. Supersedes ADR 0069's
    ai_store-mode gating."""
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
        clip_id = r.json()["clip_id"]

    fake_ai_store.ensure_uploaded.assert_called_once()
    key = fake_ai_store.ensure_uploaded.call_args.args[0]
    assert key == ("uploaded", str(clip_id))


def test_upload_ai_store_push_failure_does_not_break_upload(monkeypatch, tmp_path):
    """The AI-store push is best-effort: a transient GCS error must not fail the
    upload (offline-graceful, CLAUDE.md). The local file + proxy_cache row still
    back playback, and the annotator retries ensure_uploaded on first run."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("MEDIA_CACHE", raising=False)
    monkeypatch.delenv("CATDV_PASSWORD", raising=False)
    from backend.app import main as main_mod
    importlib.reload(main_mod)
    app = main_mod.app

    fake_ai_store = MagicMock()
    fake_ai_store.ensure_uploaded = AsyncMock(side_effect=RuntimeError("gcs down"))

    with TestClient(app) as client:
        install_live_ctx(client.app, ai_store=fake_ai_store)

        r = client.post(
            "/api/studio/uploads",
            files={"file": ("clip.mp4", b"mp4-content", "video/mp4")},
        )
        # Upload still succeeds despite the GCS failure.
        assert r.status_code == 201, r.text
        clip_id = r.json()["clip_id"]

    fake_ai_store.ensure_uploaded.assert_called_once()
    # Local copy retained on disk as the fallback.
    assert (tmp_path / "cache" / "uploads" / f"{clip_id}.mp4").read_bytes() == b"mp4-content"


def test_upload_poster_ai_store_pushes_durable(monkeypatch, tmp_path):
    app, data_dir = _make_app_ai_store(monkeypatch, tmp_path)
    fake_thumb = MagicMock()
    fake_thumb.push_durable = AsyncMock()
    fake_ai_store = MagicMock()
    fake_ai_store.ensure_uploaded = AsyncMock(return_value=MagicMock())

    with TestClient(app) as client:
        install_live_ctx(client.app, ai_store=fake_ai_store, thumbnail_service=fake_thumb)
        r = client.post(
            "/api/studio/uploads",
            files={
                "file": ("clip.mp4", b"mp4-content", "video/mp4"),
                "poster": ("p.jpg", b"\xff\xd8jpg", "image/jpeg"),
            },
        )
        assert r.status_code == 201, r.text
        clip_id = r.json()["clip_id"]

    fake_thumb.push_durable.assert_called_once()
    args = fake_thumb.push_durable.call_args.args
    assert args[0] == clip_id
    assert Path(args[1]) == data_dir / "cache" / "thumbs" / f"{clip_id}.jpg"


def test_upload_poster_local_mode_no_durable_push(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("MEDIA_CACHE", raising=False)
    monkeypatch.delenv("CATDV_PASSWORD", raising=False)
    from backend.app import main as main_mod
    importlib.reload(main_mod)
    app = main_mod.app

    fake_thumb = MagicMock()
    fake_thumb.push_durable = AsyncMock()

    with TestClient(app) as client:
        install_live_ctx(client.app, thumbnail_service=fake_thumb)
        r = client.post(
            "/api/studio/uploads",
            files={
                "file": ("clip.mp4", b"mp4-content", "video/mp4"),
                "poster": ("p.jpg", b"\xff\xd8jpg", "image/jpeg"),
            },
        )
        assert r.status_code == 201, r.text

    fake_thumb.push_durable.assert_not_called()
