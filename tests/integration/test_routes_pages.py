import dataclasses
import importlib
from datetime import UTC, datetime

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
        fetched_at=datetime.now(UTC),
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
    def __init__(self, clips: tuple[CanonicalClip, ...] = (), total: int | None = None):
        self._clips = clips
        self._total = total
        self.last_query = None

    async def list_clips(self, catalog, query):
        self.last_query = query
        return ClipPage(
            items=self._clips,
            total=self._total if self._total is not None else len(self._clips),
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
        # Decade is no longer rendered in the media-row layout (only year ·
        # duration · markers in the meta line); see the redesign spec.


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


def test_clip_detail_renders_without_timeline_when_duration_zero(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        zero_dur = dataclasses.replace(_canonical(), duration_secs=0.0)
        client.app.state.ctx.archive = FakeArchive((zero_dur,))
        r = client.get("/clips/12041")
        assert r.status_code == 200
        assert "Abramcukova_Anna_09" in r.text
        assert 'class="timeline"' not in r.text


class SpyListCacheRepo:
    """In-memory stand-in that records invalidate/get calls."""

    def __init__(self):
        self.invalidated: list[tuple[str, str]] = []
        self.entry: dict | None = None

    async def invalidate_catalog(self, conn, *, provider_id, catalog_id):
        self.invalidated.append((provider_id, catalog_id))
        return 0

    async def get(self, conn, *, provider_id, catalog_id, query_text, offset, limit):
        return self.entry


def test_refresh_query_invalidates_list_cache(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        ctx = client.app.state.ctx
        ctx.archive = FakeArchive((_canonical(),))
        spy = SpyListCacheRepo()
        ctx.clip_list_cache_repo = spy

        r = client.get("/?refresh=1")
        assert r.status_code == 200
        assert spy.invalidated == [("catdv", "881507")]


def test_no_refresh_query_does_not_invalidate(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        ctx = client.app.state.ctx
        ctx.archive = FakeArchive((_canonical(),))
        spy = SpyListCacheRepo()
        ctx.clip_list_cache_repo = spy

        r = client.get("/")
        assert r.status_code == 200
        assert spy.invalidated == []


def test_cache_age_displayed_when_entry_present(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        ctx = client.app.state.ctx
        ctx.archive = FakeArchive((_canonical(),))
        spy = SpyListCacheRepo()
        # Pretend the list cache has a row 2 minutes old.
        from datetime import datetime, timedelta, timezone
        two_min_ago = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        spy.entry = {"fetched_at": two_min_ago, "total": 1, "items": ()}
        ctx.clip_list_cache_repo = spy

        r = client.get("/")
        assert r.status_code == 200
        assert "Cached" in r.text
        assert "Refresh" in r.text
        assert "refresh=1" in r.text


def test_clips_page_marks_clips_rail_active(monkeypatch, tmp_path):
    """Clips list sets rail_active so the Clips icon gets `.active`."""
    with _make_client(monkeypatch, tmp_path) as client:
        client.app.state.ctx.archive = FakeArchive((_canonical(),))
        r = client.get("/")
        assert r.status_code == 200
        # exactly one rail button should be active; the first one is Clips
        assert 'rail-btn active' in r.text
        assert 'title="Clips"' in r.text
        # all three icons present
        assert 'rail-preview' in r.text
        assert 'title="Cache"' in r.text


def test_clip_detail_marks_preview_rail_active(monkeypatch, tmp_path):
    """Detail page activates the Preview rail icon and writes lastClipId."""
    with _make_client(monkeypatch, tmp_path) as client:
        client.app.state.ctx.archive = FakeArchive((_canonical(),))
        r = client.get("/clips/12041")
        assert r.status_code == 200
        assert 'rail-btn active' in r.text
        assert 'localStorage.setItem("catdv:lastClipId", "12041")' in r.text


def _canonical_with(
    *, clip_id=12041, name="x",
    poster_id: int | None = None,
    notes: str | None = None,
    big_notes: str | None = None,
) -> CanonicalClip:
    pd: dict = {"ID": clip_id, "name": name}
    if poster_id is not None:
        pd["posterID"] = poster_id
    if notes is not None:
        pd["notes"] = notes
    if big_notes is not None:
        pd["bigNotes"] = big_notes
    base = _canonical(clip_id=clip_id, name=name)
    return dataclasses.replace(base, provider_data=pd)


def test_clips_list_renders_poster_img_when_poster_id_present(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        clip = _canonical_with(clip_id=12041, name="C1", poster_id=882119)
        client.app.state.ctx.archive = FakeArchive((clip,))
        r = client.get("/")
        assert r.status_code == 200
        assert '/api/poster/12041?v=882119' in r.text
        assert 'loading="lazy"' in r.text


def test_clips_list_uses_fallback_when_no_poster_id(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        clip = _canonical_with(clip_id=42, name="C2")
        client.app.state.ctx.archive = FakeArchive((clip,))
        r = client.get("/")
        assert r.status_code == 200
        assert "/api/poster/42" not in r.text
        assert "poster-fallback" in r.text


def test_clips_list_shows_notes_excerpt(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        clip = _canonical_with(
            clip_id=7, name="C3", notes="LOV, STŘÍLENÍ, JELENI",
        )
        client.app.state.ctx.archive = FakeArchive((clip,))
        r = client.get("/")
        assert r.status_code == 200
        assert "LOV, STŘÍLENÍ, JELENI" in r.text
        assert "clip-row__notes" in r.text


def test_clips_list_renders_more_button_for_long_notes(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        clip = _canonical_with(
            clip_id=8, name="C4",
            notes="line a\nline b\nline c with detail",
        )
        client.app.state.ctx.archive = FakeArchive((clip,))
        r = client.get("/")
        assert r.status_code == 200
        assert "clip-row__more" in r.text


def test_clips_list_no_more_button_for_short_notes(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        clip = _canonical_with(clip_id=9, name="C5", notes="krátká")
        client.app.state.ctx.archive = FakeArchive((clip,))
        r = client.get("/")
        assert r.status_code == 200
        assert "clip-row__more" not in r.text


def test_clips_list_default_limit_is_20(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        fake = FakeArchive((_canonical(),))
        client.app.state.ctx.archive = fake
        r = client.get("/")
        assert r.status_code == 200
        assert fake.last_query.limit == 20


def test_pager_url_encodes_search_query(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        client.app.state.ctx.archive = FakeArchive((_canonical(),), total=100)
        r = client.get("/?q=hello+world%26x&offset=10&limit=10")
        assert r.status_code == 200
        assert "q=hello%20world%26x" in r.text
        assert "q=hello world&x" not in r.text
