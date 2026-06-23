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


async def test_edit_rates_version_flows_into_compute_cost(db):
    from backend.app.services.pricing import compute_cost
    from backend.app.services.telemetry_capture import TokenUsage

    svc = _service(db)
    await svc.reconcile_seeds()
    await svc.reload()

    usage = TokenUsage(tokens_in=1000)
    _, v0 = compute_cost(usage, "gemini-2.5-flash-lite")
    assert v0 == "2026-06"  # seed version

    await svc.edit_rates(
        "gemini-2.5-flash-lite",
        input_text_video_image_per_1m=0.20,
        input_audio_per_1m=0.30,
        input_cached_per_1m=0.01,
        output_per_1m=0.40,
    )
    _, v1 = compute_cost(usage, "gemini-2.5-flash-lite")
    assert v1.startswith("edit-")  # bumped version now visible to compute_cost


async def test_set_rates_creates_card_for_unpriced_model(db):
    svc = _service(db)
    await svc.reconcile_seeds()
    await svc.reload()
    assert "gemini-3.5-flash" not in pricing.rate_cards()

    await svc.set_rates(
        "gemini-3.5-flash",
        input_text_video_image_per_1m=0.15,
        input_audio_per_1m=0.25,
        input_cached_per_1m=0.02,
        output_per_1m=0.55,
    )
    rows = {r.model for r in await svc.rows()}
    assert "gemini-3.5-flash" in rows
    cards = pricing.rate_cards()  # reload happened implicitly
    assert "gemini-3.5-flash" in cards
    assert cards["gemini-3.5-flash"].input_text_video_image_per_1m == 0.15


async def test_remove_model_drops_from_rows_and_cache(db):
    svc = _service(db)
    await svc.reconcile_seeds()
    await svc.reload()
    assert "gemini-2.5-flash-lite" in pricing.rate_cards()

    await svc.remove_model("gemini-2.5-flash-lite")
    rows = {r.model for r in await svc.rows()}
    assert "gemini-2.5-flash-lite" not in rows
    assert "gemini-2.5-flash-lite" not in pricing.rate_cards()


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


async def test_set_and_get_default_resolution(db):
    svc = _service(db)
    await svc.reconcile_seeds()
    await svc.set_resolution("gemini-2.5-flash-lite", "low")
    assert await svc.default_resolution("gemini-2.5-flash-lite") == "low"


async def test_default_resolution_falls_back_to_medium_for_unknown(db):
    svc = _service(db)
    await svc.reconcile_seeds()
    assert await svc.default_resolution("not-a-model") == "medium"
