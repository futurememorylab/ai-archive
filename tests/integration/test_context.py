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
        assert ctx.prompts_repo is not None
        assert ctx.jobs_repo is not None
        assert ctx.event_bus is not None
        cur = await ctx.db.execute("SELECT count(*) FROM prompts")
        assert (await cur.fetchone())[0] == 0
    finally:
        await ctx.aclose()


@pytest.mark.asyncio
async def test_context_exposes_archive_provider_when_external_initialized(tmp_path, monkeypatch):
    from backend.app.context import AppContext
    from backend.app.settings import Settings
    from tests.fakes.fake_catdv import running_fake_catdv

    # GCS / Gemini need real credentials; stub them so we can exercise the
    # archive-wiring branch without ADC.
    import backend.app.services.gcs as gcs_mod
    import backend.app.services.gemini as gemini_mod

    class _StubGcs:
        def __init__(self, *args, **kwargs):
            self.bucket_name = "b"
            self._bucket = type("FakeBucket", (), {"exists": staticmethod(lambda: True)})()

    class _StubGemini:
        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(gcs_mod, "GcsService", _StubGcs)
    monkeypatch.setattr(gemini_mod, "GeminiService", _StubGemini)

    with running_fake_catdv() as (base_url, _):
        monkeypatch.setenv("CATDV_BASE_URL", base_url)
        monkeypatch.setenv("CATDV_USERNAME", "klientAI")
        monkeypatch.setenv("CATDV_PASSWORD", "secret")
        monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
        monkeypatch.setenv("GCP_PROJECT_ID", "p")
        monkeypatch.setenv("GCS_BUCKET_NAME", "b")
        monkeypatch.setenv("GCP_LOCATION", "europe-west3")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        s = Settings()
        ctx = await AppContext.build(s, init_external=True)
        try:
            from backend.app.archive.providers.catdv.adapter import CatdvArchiveAdapter

            assert isinstance(ctx.archive, CatdvArchiveAdapter)

            from backend.app.archive.ai_stores.gcs.adapter import GcsInputStore

            assert isinstance(ctx.ai_store, GcsInputStore)
            assert ctx.ai_store.id == "gcs:b"
            # The old `ctx.gcs` attribute is gone.
            assert not hasattr(ctx, "gcs") or ctx.gcs is None
        finally:
            await ctx.aclose()
