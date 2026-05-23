from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost/none")
    monkeypatch.setenv("CATDV_CATALOG_ID", "0")
    monkeypatch.setenv("GCP_PROJECT_ID", "x")
    monkeypatch.setenv("GCS_BUCKET_NAME", "x")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "dev")
    with TestClient(app) as c:
        yield c


def test_list_page_includes_cache_badge_column(client, monkeypatch):
    from backend.app.archive.model import CanonicalClip, ClipPage, MediaRef

    class _Archive:
        async def list_clips(self, catalog_id, q):
            clip = CanonicalClip(
                key=("catdv", "1"),
                name="Test",
                duration_secs=10.0,
                fps=25.0,
                markers=(),
                fields={},
                notes={},
                media=MediaRef(
                    mime_type="video/quicktime",
                    size_bytes=None,
                    cached_path=None,
                    upstream_handle="x",
                ),
                provider_data={},
                fetched_at=datetime.now(UTC),
            )
            return ClipPage(items=(clip,), total=1, offset=0, limit=50)

    with TestClient(app) as c:
        c.app.state.ctx.archive = _Archive()
        r = c.get("/")
    assert r.status_code == 200
    html = r.text
    assert "cache-badge" in html
    assert 'name="clip_keys"' in html
    assert "actions-dropdown" in html


def test_list_page_bulk_toolbar_actions_present(client):
    r = client.get("/")
    assert r.status_code in (200, 502, 503)
    if r.status_code == 200:
        # Single "Actions" dropdown replaces the old per-action buttons.
        assert "actions-dropdown" in r.text
        assert "Cache locally" in r.text
        assert "Remove from local cache" in r.text


def test_list_page_filter_form_renders_with_dropdowns(client):
    """Search button + cache/anno filter dropdowns are present in the toolbar."""
    from backend.app.archive.model import ClipPage

    class _Archive:
        async def list_clips(self, catalog_id, q):
            return ClipPage(items=(), total=0, offset=0, limit=50)

    with TestClient(app) as c:
        c.app.state.ctx.archive = _Archive()
        r = c.get("/")
    assert r.status_code == 200
    html = r.text
    assert "Search clips" in html  # prominent placeholder
    assert "search-icon" in html
    assert 'name="cache"' in html
    assert 'name="anno"' in html
    assert ">For review<" in html


def test_list_page_filter_path_uses_local_first(client, tmp_path):
    """When ?cache=local is set, only locally-cached clips are returned and
    the CatDV list_clips path is skipped (the fake archive's get_clip is
    used instead to hydrate the single candidate)."""
    from backend.app.archive.model import CanonicalClip, MediaRef

    class _Archive:
        calls: list[str] = []

        async def list_clips(self, catalog_id, q):  # pragma: no cover
            raise AssertionError("list_clips should not be called with filters active")

        async def get_clip(self, clip_id):
            self.calls.append(clip_id)
            return CanonicalClip(
                key=("catdv", clip_id),
                name=f"Clip {clip_id}",
                duration_secs=5.0,
                fps=25.0,
                markers=(),
                fields={},
                notes={},
                media=MediaRef(
                    mime_type="video/quicktime",
                    size_bytes=None,
                    cached_path=None,
                    upstream_handle=clip_id,
                ),
                provider_data={},
                fetched_at=datetime.now(UTC),
            )

    archive = _Archive()
    with TestClient(app) as c:
        c.app.state.ctx.archive = archive

        async def _seed():
            db = c.app.state.ctx.db
            await db.execute(
                """
                INSERT INTO proxy_cache
                  (catdv_clip_id, provider_id, provider_clip_id,
                   file_path, size_bytes, etag, downloaded_at, last_used_at)
                VALUES (42, 'catdv', '42', '/tmp/42.mov', 1024, NULL,
                        '2026-05-22', '2026-05-22')
                """
            )
            await db.commit()

        import asyncio

        asyncio.run(_seed())
        r = c.get("/?cache=local")

    assert r.status_code == 200
    assert "Clip 42" in r.text
    assert archive.calls == ["42"]
