"""Guard: every model in the gemini_generation_model catalog has a SEED_RATE_CARDS entry.

If a model is added to the catalog without a matching rate card it will show
'no rate card' in the admin UI and return NULL cost for every annotation run.
This test fails fast when that happens so the engineer knows to add the card
in the same PR.
"""

from backend.app.enums.registry import ENUM_REGISTRY
from backend.app.services.pricing import SEED_RATE_CARDS


def test_every_catalog_model_has_a_seed_rate_card():
    catalog_models = {
        spec.value
        for spec in ENUM_REGISTRY["gemini_generation_model"].values
    }
    missing = catalog_models - set(SEED_RATE_CARDS)
    assert not missing, (
        f"The following gemini_generation_model catalog entries have no entry in "
        f"SEED_RATE_CARDS — add a RateCard for each before merging:\n"
        + "\n".join(f"  {m}" for m in sorted(missing))
    )
