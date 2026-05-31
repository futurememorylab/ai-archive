import importlib
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from backend.app.archive.model import CanonicalClip, ClipPage, MediaRef


def _canonical(clip_id: int, name: str) -> CanonicalClip:
    return CanonicalClip(
        key=("catdv", str(clip_id)),
        name=name,
        duration_secs=0.0,
        fps=25.0,
        markers=tuple(),
        fields={},
        notes={},
        media=MediaRef(
            mime_type="video/quicktime",
            size_bytes=None,
            cached_path=None,
            upstream_handle=str(clip_id),
        ),
        provider_data={"ID": clip_id, "name": name},
        fetched_at=datetime.now(UTC),
    )


def test_list_clips_proxies_to_catdv(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    from backend.app import main as main_mod

    importlib.reload(main_mod)
    app = main_mod.app

    with TestClient(app) as client:
        from tests._helpers.live_ctx import install_live_ctx

        class FakeArchive:
            async def list_clips(self, catalog, query):
                return ClipPage(
                    items=(_canonical(1, "x"),),
                    total=1,
                    offset=query.offset,
                    limit=query.limit,
                )

            async def get_clip(self, clip_id_str):
                return _canonical(int(clip_id_str), "x")

        install_live_ctx(client.app, archive=FakeArchive())

        r = client.get("/api/catdv/clips?limit=10")
        assert r.status_code == 200
        assert r.json()["total"] == 1
        assert r.json()["clips"][0]["ID"] == 1

        r = client.get("/api/catdv/clips/1")
        assert r.status_code == 200
        assert r.json()["ID"] == 1
