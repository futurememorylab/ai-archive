"""RunTelemetryRepo: insert round-trip; aggregate reads exclude
MAX_TOKENS and error rows (they would poison estimates)."""

import uuid

import pytest

from backend.app.models.telemetry import RunTelemetryRecord
from backend.app.repositories.run_telemetry import RunTelemetryRepo


def _rec(**over) -> RunTelemetryRecord:
    base = dict(
        event_id=str(uuid.uuid4()),
        occurred_at="2026-06-07T12:00:00+00:00",
        install_id="inst-1",
        kind="studio",
        model="gemini-2.5-flash-lite",
        status="ok",
        media_kind="video+audio",
        media_duration_secs=10.0,
        prompt_hash="h" * 64,
        tokens_in=3000, tokens_in_video=2900,
        tokens_out=100, tokens_thinking=20,
        finish_reason="STOP",
        attrs={"note": "test"},
    )
    base.update(over)
    return RunTelemetryRecord(**base)


@pytest.mark.asyncio
async def test_insert_roundtrip(db):
    repo = RunTelemetryRepo()
    await repo.insert(db, _rec())
    cur = await db.execute(
        "SELECT kind, model, tokens_in, tokens_thinking, attrs FROM run_telemetry"
    )
    row = await cur.fetchone()
    assert row[0] == "studio" and row[1] == "gemini-2.5-flash-lite"
    assert row[2] == 3000 and row[3] == 20
    assert '"note"' in row[4]


@pytest.mark.asyncio
async def test_input_ratios_only_ok_rows_with_media_tokens(db):
    repo = RunTelemetryRepo()
    await repo.insert(db, _rec(tokens_in_video=2900, media_duration_secs=10.0))
    await repo.insert(db, _rec(status="error"))          # excluded
    await repo.insert(db, _rec(tokens_in_video=0))        # excluded (no signal)
    ratios = await repo.recent_input_ratios(
        db, model="gemini-2.5-flash-lite", media_kind="video+audio"
    )
    assert ratios == [290.0]


@pytest.mark.asyncio
async def test_output_rates_exclude_max_tokens_and_filter_by_hash(db):
    repo = RunTelemetryRepo()
    await repo.insert(db, _rec(tokens_out=100, tokens_thinking=20))   # 12/s
    await repo.insert(db, _rec(finish_reason="MAX_TOKENS"))           # excluded
    await repo.insert(db, _rec(prompt_hash="x" * 64, tokens_out=500)) # other prompt
    rates = await repo.recent_output_rates(
        db, model="gemini-2.5-flash-lite", media_kind="video+audio",
        prompt_hash="h" * 64,
    )
    assert rates == [12.0]
    all_rates = await repo.recent_output_rates(
        db, model="gemini-2.5-flash-lite", media_kind="video+audio",
    )
    assert sorted(all_rates) == [12.0, 52.0]


@pytest.mark.asyncio
async def test_output_rates_images_are_per_item(db):
    repo = RunTelemetryRepo()
    await repo.insert(db, _rec(
        media_kind="image", media_duration_secs=None,
        tokens_out=800, tokens_thinking=0,
    ))
    rates = await repo.recent_output_rates(
        db, model="gemini-2.5-flash-lite", media_kind="image",
    )
    assert rates == [800.0]
