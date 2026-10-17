"""Clip detail no longer shows a LIVE pill or a version History dropdown.

Versions still exist in the database (every apply records one), but the
annotation panel doesn't surface them: "In archive" and "Draft" are the only
states a user sees.
"""

import asyncio
import importlib
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from backend.app.archive.model import CanonicalClip, MediaRef
from backend.app.models.annotation import ClipVersion
from tests._helpers.live_ctx import install_live_ctx


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


def test_clip_detail_with_a_live_version_shows_no_live_pill_or_history(monkeypatch, tmp_path):
    clip_id = 12041
    with _make_client(monkeypatch, tmp_path) as client:
        ctx = client.app.state.core_ctx

        async def _seed() -> None:
            await ctx.clip_versions_repo.insert(
                ctx.db,
                ClipVersion(
                    catdv_clip_id=clip_id,
                    version_num=3,
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
        assert "data-history-menu" not in r.text
        assert "data-publish-status" not in r.text
        assert "Live v" not in r.text
        assert "stays restorable" not in r.text
        assert client.get(f"/clips/{clip_id}/version-panel").status_code == 404
