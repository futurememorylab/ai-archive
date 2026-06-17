"""Render test for the clip-detail History dropdown + publish-status headline pill.

Seeds a clip with a live ClipVersion and asserts the rendered HTML contains:
- data-history-menu (the history dropdown anchor element)
- data-publish-status attribute (the headline pill wrapper)
- 'Live v' text (confirming the live-version label is rendered)
"""
import asyncio
import importlib

from fastapi.testclient import TestClient

from backend.app.archive.model import CanonicalClip, FieldValue, Marker, MediaRef, Timecode
from backend.app.models.annotation import ClipVersion
from tests._helpers.live_ctx import install_live_ctx
from datetime import UTC, datetime


def _canonical(clip_id: int = 12041) -> CanonicalClip:
    return CanonicalClip(
        key=("catdv", str(clip_id)),
        name="Test Clip History",
        duration_secs=120.0,
        fps=25.0,
        markers=(),
        fields={},
        notes={},
        media=MediaRef(
            mime_type="video/quicktime",
            size_bytes=None,
            cached_path=None,
            upstream_handle=str(clip_id),
        ),
        provider_data={"ID": clip_id, "name": "Test Clip History"},
        fetched_at=datetime.now(UTC),
    )


class FakeArchive:
    def __init__(self, clip: CanonicalClip):
        self._clip = clip

    async def get_clip(self, clip_id_str: str) -> CanonicalClip:
        if clip_id_str == str(self._clip.key[1]):
            return self._clip
        from backend.app.archive.errors import ProviderError
        raise ProviderError("not found")


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


def test_clip_detail_shows_history_menu_and_publish_status(monkeypatch, tmp_path):
    """A clip with a live ClipVersion renders the history dropdown and headline pill."""
    clip_id = 12041
    with _make_client(monkeypatch, tmp_path) as client:
        ctx = client.app.state.core_ctx

        async def _seed() -> None:
            repo = ctx.clip_versions_repo
            await repo.insert(
                ctx.db,
                ClipVersion(
                    catdv_clip_id=clip_id,
                    version_num=1,
                    snapshot={},
                    publish_state="live",
                    origin="publish",
                    model=None,
                    author="test@example.com",
                ),
            )

        asyncio.run(_seed())
        install_live_ctx(client.app, archive=FakeArchive(_canonical(clip_id)))

        r = client.get(f"/clips/{clip_id}")
        assert r.status_code == 200
        # History dropdown anchor element must be present.
        assert "data-history-menu" in r.text
        # Headline publish-status wrapper must carry data-publish-status.
        assert "data-publish-status" in r.text
        # The live version label must appear somewhere in the rendered HTML.
        assert "Live v" in r.text


def test_clip_detail_no_versions_shows_no_live_pill(monkeypatch, tmp_path):
    """A clip with no versions renders the history menu but no 'Live v' label."""
    clip_id = 12041
    with _make_client(monkeypatch, tmp_path) as client:
        install_live_ctx(client.app, archive=FakeArchive(_canonical(clip_id)))

        r = client.get(f"/clips/{clip_id}")
        assert r.status_code == 200
        # History dropdown must still be present (empty state).
        assert "data-history-menu" in r.text
        # No live version = no 'Live v' text.
        assert "Live v" not in r.text
