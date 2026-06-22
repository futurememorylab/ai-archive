"""ModelConfigRepo: seed-insert is idempotent; edits bump version; soft delete."""

import pytest

from backend.app.repositories.model_config import ModelConfigRepo, ModelConfigRow

pytestmark = pytest.mark.asyncio


def _card(model="m1"):
    return ModelConfigRow(
        model=model,
        input_text_video_image_per_1m=0.10,
        input_audio_per_1m=0.30,
        input_cached_per_1m=0.01,
        output_per_1m=0.40,
        source_url="https://example.test",
        default_media_resolution="medium",
        pricing_version="2026-06",
        updated_at="2026-06-22T00:00:00+00:00",
        removed=0,
        created_at="2026-06-22T00:00:00+00:00",
    )


async def test_upsert_seed_inserts_then_is_idempotent(db):
    repo = ModelConfigRepo()
    await repo.upsert_seed(db, _card(), commit=True)
    # Second seed with different rates must NOT overwrite the first.
    changed = _card()
    changed.output_per_1m = 99.0
    await repo.upsert_seed(db, changed, commit=True)
    row = await repo.get(db, "m1")
    assert row is not None
    assert row.output_per_1m == 0.40  # unchanged


async def test_all_live_excludes_removed(db):
    repo = ModelConfigRepo()
    await repo.upsert_seed(db, _card("keep"), commit=True)
    await repo.upsert_seed(db, _card("gone"), commit=True)
    await repo.soft_delete(db, "gone", commit=True)
    models = {r.model for r in await repo.all_live(db)}
    assert models == {"keep"}
    # get() intentionally still returns the tombstone (so reconcile won't
    # silently re-seed a deleted model); only all_live filters it out.
    gone = await repo.get(db, "gone")
    assert gone is not None and gone.removed == 1


async def test_update_rates_bumps_version(db):
    repo = ModelConfigRepo()
    await repo.upsert_seed(db, _card("m1"), commit=True)
    await repo.update_rates(
        db,
        "m1",
        input_text_video_image_per_1m=0.20,
        input_audio_per_1m=0.30,
        input_cached_per_1m=0.01,
        output_per_1m=0.40,
        pricing_version="edit-2026-06-22T10:00:00Z",
        commit=True,
    )
    row = await repo.get(db, "m1")
    assert row.input_text_video_image_per_1m == 0.20
    assert row.pricing_version == "edit-2026-06-22T10:00:00Z"
