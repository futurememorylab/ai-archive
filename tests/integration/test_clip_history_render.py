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


def test_clip_history_diff_summary_rendered(monkeypatch, tmp_path):
    """A version with a diff dict renders a compact summary line in the history row."""
    clip_id = 12042
    with _make_client(monkeypatch, tmp_path) as client:
        ctx = client.app.state.core_ctx

        async def _seed() -> None:
            repo = ctx.clip_versions_repo
            # v1: no diff (backfilled baseline)
            await repo.insert(
                ctx.db,
                ClipVersion(
                    catdv_clip_id=clip_id,
                    version_num=1,
                    snapshot={},
                    diff=None,
                    publish_state="superseded",
                    origin="publish",
                    model=None,
                    author=None,
                ),
            )
            # v2: has a diff with markers, changed field, and notes
            await repo.insert(
                ctx.db,
                ClipVersion(
                    catdv_clip_id=clip_id,
                    version_num=2,
                    snapshot={},
                    diff={
                        "markers_added": 2,
                        "fields_changed": {"pragafilm.genre": "thriller"},
                        "notes_changed": True,
                        "big_notes_changed": False,
                    },
                    publish_state="live",
                    origin="publish",
                    model=None,
                    author="editor@example.com",
                ),
            )

        asyncio.run(_seed())
        install_live_ctx(client.app, archive=FakeArchive(_canonical(clip_id)))

        r = client.get(f"/clips/{clip_id}")
        assert r.status_code == 200

        # v2's diff summary must contain the marker count, field short-name, and notes indicator.
        assert "+2 markers" in r.text, "Expected '+2 markers' in diff summary"
        assert "genre" in r.text, "Expected 'genre' (short field name) in diff summary"
        assert "notes" in r.text, "Expected 'notes' indicator in diff summary"

        # big_notes_changed=False must NOT produce 'bigNotes' in the summary.
        assert "bigNotes" not in r.text, "Did not expect 'bigNotes' when big_notes_changed is False"


def test_clip_history_null_diff_renders_without_error(monkeypatch, tmp_path):
    """A version with diff=None renders without error and without a diff summary line."""
    clip_id = 12043
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
                    diff=None,
                    publish_state="live",
                    origin="publish",
                    model=None,
                    author=None,
                ),
            )

        asyncio.run(_seed())
        install_live_ctx(client.app, archive=FakeArchive(_canonical(clip_id)))

        r = client.get(f"/clips/{clip_id}")
        assert r.status_code == 200
        assert "data-history-menu" in r.text
        # No diff summary data-attribute or marker summary should appear.
        assert "data-diff-summary" not in r.text
