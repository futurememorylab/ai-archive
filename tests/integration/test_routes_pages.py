import importlib
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from backend.app.archive.errors import ProviderError
from backend.app.archive.model import (
    CanonicalClip,
    ClipPage,
    FieldValue,
    Marker,
    MediaRef,
    Timecode,
)


def _canonical(clip_id: int = 12041, name: str = "Abramcukova_Anna_09") -> CanonicalClip:
    return CanonicalClip(
        key=("catdv", str(clip_id)),
        name=name,
        duration_secs=522.0,
        fps=25.0,
        markers=(
            Marker(
                name="Anna na zahradě",
                in_=Timecode(secs=83.48, fps=25.0),
                out=Timecode(secs=105.12, fps=25.0),
                description="Detailní záběr",
            ),
        ),
        fields={
            "pragafilm.dekáda.natočení": FieldValue(
                identifier="pragafilm.dekáda.natočení", value="30.léta"
            ),
            "pragafilm.rok.natočení": FieldValue(
                identifier="pragafilm.rok.natočení", value=["1932"], is_multi=True
            ),
        },
        notes={},
        media=MediaRef(
            mime_type="video/quicktime",
            size_bytes=None,
            cached_path=None,
            upstream_handle=str(clip_id),
        ),
        provider_data={
            "ID": clip_id,
            "name": name,
            "notes": "krátká poznámka",
        },
        fetched_at=datetime.now(timezone.utc),
    )


def _make_client(monkeypatch, tmp_path):
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
    return TestClient(main_mod.app)


class FakeArchive:
    def __init__(self, clips: tuple[CanonicalClip, ...] = ()):
        self._clips = clips
        self.last_query = None

    async def list_clips(self, catalog, query):
        self.last_query = query
        return ClipPage(
            items=self._clips, total=len(self._clips),
            offset=query.offset, limit=query.limit,
        )

    async def get_clip(self, clip_id_str):
        for c in self._clips:
            if c.key[1] == clip_id_str:
                return c
        raise ProviderError("not found")


def test_clips_list_returns_full_page(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        client.app.state.ctx.archive = FakeArchive((_canonical(),))
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "<!doctype html>" in r.text.lower()
        assert "Abramcukova_Anna_09" in r.text
        assert "1932" in r.text
        assert "30.léta" in r.text


def test_clips_list_htmx_returns_partial(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        client.app.state.ctx.archive = FakeArchive((_canonical(),))
        r = client.get("/", headers={"HX-Request": "true"})
        assert r.status_code == 200
        assert "<!doctype html>" not in r.text.lower()
        assert "Abramcukova_Anna_09" in r.text


def test_clips_list_passes_query_to_adapter(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        fake = FakeArchive((_canonical(),))
        client.app.state.ctx.archive = fake
        r = client.get("/?q=Anna&offset=20&limit=10")
        assert r.status_code == 200
        assert fake.last_query.text == "Anna"
        assert fake.last_query.offset == 20
        assert fake.last_query.limit == 10


def test_clip_detail_renders(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        client.app.state.ctx.archive = FakeArchive((_canonical(),))
        r = client.get("/clips/12041")
        assert r.status_code == 200
        assert "Abramcukova_Anna_09" in r.text
        assert "Anna na zahradě" in r.text
        assert "/api/media/12041" in r.text


def test_clip_detail_404_when_missing(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        client.app.state.ctx.archive = FakeArchive(())
        r = client.get("/clips/99999")
        assert r.status_code == 404


def test_static_mount(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        r = client.get("/static/app.css")
        assert r.status_code == 200
        assert "text/css" in r.headers["content-type"]
