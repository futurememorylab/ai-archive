"""Direct-to-bucket Studio uploads (cloud): start → browser PUT → complete.

Cloud Run refuses requests over 32 MB, so in ai_store mode the browser PUTs
the file to a signed URL and the app only handles the start and the finish.
A real DirectUpload over a fake GCS and the real ai_store_files repo shows the
finished clip is registered where playback and annotation look for it."""

import importlib
from functools import partial

import pytest
from fastapi.testclient import TestClient

from backend.app.services.direct_upload import DirectUpload
from backend.app.services.gcs import BlobMeta
from backend.app.uploaded_ids import is_uploaded
from tests._helpers.live_ctx import install_live_ctx


class FakeGcs:
    """What the browser's PUT leaves in the bucket is set with `receive`."""

    bucket_name = "bucket"

    def __init__(self):
        self.blobs: dict[int, BlobMeta] = {}
        self.deleted: list[int] = []

    def signed_upload_url(self, clip_id, mime, *, expires_s):
        return f"https://storage.example/{clip_id}?type={mime}"

    def blob_meta(self, clip_id):
        return self.blobs.get(clip_id)

    def gs_uri(self, clip_id):
        return f"gs://bucket/instances/test/uploads/{clip_id}.mov"

    def delete(self, clip_id):
        self.deleted.append(clip_id)
        self.blobs.pop(clip_id, None)

    def receive(self, clip_id, size):
        self.blobs[clip_id] = BlobMeta(size=size, content_type="video/mp4", md5="bWQ1")


def _boot(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("MEDIA_CACHE", "ai_store")
    monkeypatch.delenv("CATDV_PASSWORD", raising=False)
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    return main_mod.app


@pytest.fixture
def cloud(monkeypatch, tmp_path):
    app = _boot(monkeypatch, tmp_path)
    gcs = FakeGcs()
    with TestClient(app) as client:
        core = app.state.core_ctx
        direct = DirectUpload(
            gcs=gcs,
            files_repo=core.ai_store_files_repo,
            store_id="gcs:bucket",
            db_provider=lambda: core.db,
        )
        install_live_ctx(client.app, direct_upload=direct)
        yield client, gcs, core


BIG = 60 * 1024 * 1024  # over Cloud Run's 32 MiB request limit


def _ai_store_row(client, core, clip_id):
    get = partial(core.ai_store_files_repo.get, store_id="gcs:bucket", clip_id=clip_id)
    return client.portal.call(get, core.db)


def _start(client, **over):
    body = {"filename": "Duck and Cover.mp4", "mime": "video/mp4", "size_bytes": BIG}
    body.update(over)
    return client.post("/api/studio/uploads/direct", json=body)


def test_start_reserves_a_clip_and_signs_a_put(cloud):
    client, _, core = cloud
    r = _start(client, duration_secs=540.0)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["direct"] is True
    assert is_uploaded(body["clip_id"])
    assert body["upload_url"].startswith("https://storage.example/")
    assert body["mime"] == "video/mp4"
    row = client.portal.call(core.uploaded_clips_repo.get, core.db, body["clip_id"])
    assert row["size_bytes"] == BIG
    assert row["duration_secs"] == 540.0


def test_start_refuses_a_wrong_type_or_too_big(cloud):
    client, _, _ = cloud
    assert _start(client, mime="video/quicktime").status_code == 415
    assert _start(client, size_bytes=501 * 1024 * 1024).status_code == 413


def test_complete_registers_the_clip_and_adds_it_to_the_set(cloud):
    client, gcs, core = cloud
    clip_id = _start(client).json()["clip_id"]
    gcs.receive(clip_id, BIG)  # the browser's PUT landed

    r = client.post(
        f"/api/studio/uploads/direct/{clip_id}/complete",
        files={"poster": ("p.jpg", b"jpeg-bytes", "image/jpeg")},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 201, r.text
    assert "studio-clip-card" in r.text
    assert "Duck and Cover.mp4" in r.text

    # Registered as the clip's AI-store copy: playback and annotation find it.
    row = _ai_store_row(client, core, clip_id)
    assert row["gcs_uri"] == f"gs://bucket/instances/test/uploads/{clip_id}.mov"
    assert row["size_bytes"] == BIG
    # In the default Uploads set; no bytes on the app's disk.
    sets = client.get("/api/studio/sets?source=uploaded").json()
    assert sets[0]["name"] == "Uploads"
    assert [c["clip_id"] for c in client.get(f"/api/studio/sets/{sets[0]['id']}/clips").json()] == [
        clip_id
    ]
    assert client.portal.call(core.proxy_cache_repo.get, core.db, clip_id) is None


def test_complete_into_an_explicit_set(cloud):
    client, gcs, _ = cloud
    sid = client.post("/api/studio/sets?source=uploaded", json={"name": "Workshop"}).json()["id"]
    clip_id = _start(client).json()["clip_id"]
    gcs.receive(clip_id, BIG)
    r = client.post(f"/api/studio/uploads/direct/{clip_id}/complete", data={"set_id": str(sid)})
    assert r.status_code == 201, r.text
    assert r.json() == {"clip_id": clip_id, "set_id": sid}


def test_complete_refuses_when_the_file_never_arrived(cloud):
    client, _, core = cloud
    clip_id = _start(client).json()["clip_id"]
    r = client.post(f"/api/studio/uploads/direct/{clip_id}/complete")
    assert r.status_code == 409
    assert "never reached storage" in r.json()["detail"]
    assert _ai_store_row(client, core, clip_id) is None


def test_complete_refuses_a_file_of_another_size(cloud):
    client, gcs, _ = cloud
    clip_id = _start(client, size_bytes=1000).json()["clip_id"]
    gcs.receive(clip_id, BIG)  # bigger than announced (and than the cap allows)
    assert client.post(f"/api/studio/uploads/direct/{clip_id}/complete").status_code == 409


def test_abort_drops_the_reserved_clip_and_partial_blob(cloud):
    client, gcs, core = cloud
    clip_id = _start(client).json()["clip_id"]
    gcs.receive(clip_id, 123)
    assert client.delete(f"/api/studio/uploads/direct/{clip_id}").status_code == 204
    assert gcs.deleted == [clip_id]
    assert client.portal.call(core.uploaded_clips_repo.get, core.db, clip_id) is None


def test_abort_leaves_a_finished_clip_alone(cloud):
    client, gcs, core = cloud
    clip_id = _start(client).json()["clip_id"]
    gcs.receive(clip_id, BIG)
    client.post(f"/api/studio/uploads/direct/{clip_id}/complete")
    assert client.delete(f"/api/studio/uploads/direct/{clip_id}").status_code == 404
    assert client.post(f"/api/studio/uploads/direct/{clip_id}/complete").status_code == 404
    assert gcs.deleted == []
    assert client.portal.call(core.uploaded_clips_repo.get, core.db, clip_id) is not None


def test_local_runs_upload_through_the_app(monkeypatch, tmp_path):
    """Without a DirectUpload (media_cache=local) start says so and the browser
    falls back to POST /api/studio/uploads, which has no size limit locally."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("MEDIA_CACHE", raising=False)
    monkeypatch.delenv("CATDV_PASSWORD", raising=False)
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    with TestClient(main_mod.app) as client:
        r = _start(client)
        assert r.status_code == 200
        assert r.json() == {"direct": False}
        assert client.post("/api/studio/uploads/direct/1000000001/complete").status_code == 409
