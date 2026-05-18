import pytest

from backend.app.context import AppContext
from backend.app.settings import Settings


@pytest.mark.asyncio
async def test_build_context_from_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "u")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    settings = Settings()
    ctx = await AppContext.build(settings, init_external=False)
    try:
        assert ctx.settings is settings
        assert ctx.templates_repo is not None
        assert ctx.jobs_repo is not None
        assert ctx.event_bus is not None
        cur = await ctx.db.execute("SELECT count(*) FROM templates")
        assert (await cur.fetchone())[0] == 0
    finally:
        await ctx.aclose()
