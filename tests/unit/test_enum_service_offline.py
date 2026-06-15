import pytest

from backend.app.context import CoreCtx
from backend.app.settings import load_settings


@pytest.mark.asyncio
async def test_enum_service_on_core_ctx_works_offline(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    ctx = await CoreCtx.build(load_settings())
    try:
        # reconcile ran during build → DB materialised, served offline
        models = await ctx.enum_service.generation_models()
        assert len(models) == 8
        assert await ctx.enum_service.generation_default() == "gemini-2.5-flash-lite"
    finally:
        await ctx.aclose()
