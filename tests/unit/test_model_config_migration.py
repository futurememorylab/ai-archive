"""0024 creates model_config with the expected columns."""

import pytest

pytestmark = pytest.mark.asyncio


async def test_model_config_table_exists_with_columns(db):
    cur = await db.execute("PRAGMA table_info(model_config)")
    cols = {row[1] for row in await cur.fetchall()}
    assert cols == {
        "model",
        "input_text_video_image_per_1m",
        "input_audio_per_1m",
        "input_cached_per_1m",
        "output_per_1m",
        "source_url",
        "default_media_resolution",
        "pricing_version",
        "updated_at",
        "removed",
        "created_at",
    }


async def test_model_config_primary_key_is_model(db):
    cur = await db.execute("PRAGMA table_info(model_config)")
    pk_cols = [row[1] for row in await cur.fetchall() if row[5] == 1]
    assert pk_cols == ["model"]
