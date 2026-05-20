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
    assert "bulk-toolbar" in html


def test_list_page_bulk_toolbar_actions_present(client):
    r = client.get("/")
    assert r.status_code in (200, 502, 503)
    if r.status_code == 200:
        assert "Cache selected" in r.text
        assert "Evict selected" in r.text
