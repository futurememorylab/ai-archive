"""Migration 0016: run_telemetry + app_meta exist; install_id is stable."""

import pytest

from backend.app.repositories.app_meta import get_or_create_install_id


@pytest.mark.asyncio
async def test_run_telemetry_table_exists(db):
    cur = await db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name IN ('run_telemetry', 'app_meta')"
    )
    names = {r[0] for r in await cur.fetchall()}
    assert names == {"run_telemetry", "app_meta"}


@pytest.mark.asyncio
async def test_run_telemetry_required_columns(db):
    cur = await db.execute("PRAGMA table_info(run_telemetry)")
    cols = {r[1] for r in await cur.fetchall()}
    required = {
        "event_id",
        "occurred_at",
        "install_id",
        "app_version",
        "kind",
        "archive_id",
        "user_ref",
        "job_id",
        "clip_id",
        "clip_name",
        "prompt_version_id",
        "prompt_hash",
        "schema_hash",
        "prompt_chars_rendered",
        "model",
        "media_kind",
        "media_duration_secs",
        "media_width",
        "media_height",
        "media_fps",
        "media_bytes",
        "media_ext",
        "media_resolution_setting",
        "preprocess",
        "vertex_project",
        "vertex_location",
        "ai_store_kind",
        "status",
        "error_class",
        "finish_reason",
        "attempt_count",
        "duration_s",
        "tokens_in",
        "tokens_in_text",
        "tokens_in_video",
        "tokens_in_audio",
        "tokens_in_image",
        "tokens_cached",
        "tokens_out",
        "tokens_thinking",
        "cost_usd",
        "pricing_version",
        "est_tokens_in",
        "est_tokens_out_p50",
        "est_tokens_out_p90",
        "est_cost_usd_p50",
        "est_cost_usd_p90",
        "est_confidence",
        "output_chars",
        "review_item_count",
        "attrs",
        "sent_at",
        "send_attempts",
    }
    missing = required - cols
    assert not missing, f"missing columns: {missing}"


@pytest.mark.asyncio
async def test_install_id_created_once_and_stable(db):
    a = await get_or_create_install_id(db)
    b = await get_or_create_install_id(db)
    assert a == b
    assert len(a) == 36  # uuid4 canonical form
