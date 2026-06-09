"""Ingest route: store + index + thumbnail + set membership for uploads."""

import importlib

import pytest
from fastapi.testclient import TestClient

from backend.app.uploaded_ids import is_uploaded


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
