"""After CoreCtx.build, model_config is seeded and the cache is DB-backed."""

import pytest

from backend.app.context import CoreCtx
from backend.app.services import pricing
from backend.app.settings import load_settings

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _reset_cards():
    yield
    pricing.set_rate_cards(pricing.SEED_RATE_CARDS)


async def test_build_seeds_and_loads_pricing(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    ctx = await CoreCtx.build(load_settings())
    try:
        rows = await ctx.pricing_service.rows()
        assert {r.model for r in rows} == set(pricing.SEED_RATE_CARDS)
        assert "gemini-2.5-flash-lite" in pricing.rate_cards()
    finally:
        await ctx.aclose()
