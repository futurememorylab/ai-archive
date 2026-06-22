"""PricingService seeds model_config from SEED_RATE_CARDS and loads it into cache."""

import pytest

from backend.app.repositories.model_config import ModelConfigRepo
from backend.app.services import pricing
from backend.app.services.pricing_service import PricingService

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _reset_cards():
    yield
    pricing.set_rate_cards(pricing.SEED_RATE_CARDS)


def _service(db):
    return PricingService(db_provider=lambda: db, repo=ModelConfigRepo())


async def test_reconcile_seeds_all_seed_models(db):
    svc = _service(db)
    await svc.reconcile_seeds()
    rows = await ModelConfigRepo().all_live(db)
    assert {r.model for r in rows} == set(pricing.SEED_RATE_CARDS)


async def test_reload_populates_active_cache_from_db(db):
    svc = _service(db)
    await svc.reconcile_seeds()
    pricing.set_rate_cards({})
    await svc.reload()
    cards = pricing.rate_cards()
    assert "gemini-2.5-flash-lite" in cards
    seed = pricing.SEED_RATE_CARDS["gemini-2.5-flash-lite"]
    assert cards["gemini-2.5-flash-lite"].output_per_1m == seed.output_per_1m


async def test_reconcile_does_not_clobber_edits(db):
    svc = _service(db)
    await svc.reconcile_seeds()
    await ModelConfigRepo().update_rates(
        db,
        "gemini-2.5-flash-lite",
        input_text_video_image_per_1m=9.99,
        input_audio_per_1m=0.30,
        input_cached_per_1m=0.01,
        output_per_1m=0.40,
        pricing_version="edit-x",
        commit=True,
    )
    await svc.reconcile_seeds()  # second boot
    row = await ModelConfigRepo().get(db, "gemini-2.5-flash-lite")
    assert row.input_text_video_image_per_1m == 9.99  # edit survived
