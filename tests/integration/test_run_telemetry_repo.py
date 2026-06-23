"""RunTelemetryRepo: insert round-trip; aggregate reads exclude
MAX_TOKENS and error rows (they would poison estimates)."""

import pytest

from backend.app.models.telemetry import RunTelemetryRecord
from backend.app.repositories.run_telemetry import RunTelemetryRepo


def _rec(**over) -> RunTelemetryRecord:
    base = dict(
        occurred_at="2026-06-07T12:00:00+00:00",
        install_id="inst-1",
        kind="studio",
        model="gemini-2.5-flash-lite",
        status="ok",
        media_kind="video+audio",
        media_duration_secs=10.0,
        prompt_hash="h" * 64,
        tokens_in=3000,
        tokens_in_video=2900,
        tokens_out=100,
        tokens_thinking=20,
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
    await repo.insert(db, _rec(status="error"))  # excluded
    await repo.insert(db, _rec(tokens_in_video=0))  # excluded (no signal)
    ratios = await repo.recent_input_ratios(
        db, model="gemini-2.5-flash-lite", media_kind="video+audio"
    )
    assert ratios == [290.0]


@pytest.mark.asyncio
async def test_output_rates_exclude_max_tokens_and_filter_by_hash(db):
    repo = RunTelemetryRepo()
    await repo.insert(db, _rec(tokens_out=100, tokens_thinking=20))  # 12/s
    await repo.insert(db, _rec(finish_reason="MAX_TOKENS"))  # excluded
    await repo.insert(db, _rec(prompt_hash="x" * 64, tokens_out=500))  # other prompt
    rates = await repo.recent_output_rates(
        db,
        model="gemini-2.5-flash-lite",
        media_kind="video+audio",
        prompt_hash="h" * 64,
    )
    assert rates == [12.0]
    all_rates = await repo.recent_output_rates(
        db,
        model="gemini-2.5-flash-lite",
        media_kind="video+audio",
    )
    assert sorted(all_rates) == [12.0, 52.0]


@pytest.mark.asyncio
async def test_output_rates_images_are_per_item(db):
    repo = RunTelemetryRepo()
    await repo.insert(
        db,
        _rec(
            media_kind="image",
            media_duration_secs=None,
            tokens_out=800,
            tokens_thinking=0,
        ),
    )
    rates = await repo.recent_output_rates(
        db,
        model="gemini-2.5-flash-lite",
        media_kind="image",
    )
    assert rates == [800.0]


@pytest.mark.asyncio
async def test_output_rates_limit_and_recency_order(db):
    repo = RunTelemetryRepo()
    # Three runs with distinct rates, inserted oldest→newest: 10/s, 20/s, 30/s.
    for out in (100, 200, 300):
        await repo.insert(db, _rec(tokens_out=out, tokens_thinking=0))
    rates = await repo.recent_output_rates(
        db, model="gemini-2.5-flash-lite", media_kind="video+audio", limit=2
    )
    assert rates == [30.0, 20.0]  # newest first, capped at limit


@pytest.mark.asyncio
async def test_input_ratios_audio_branch(db):
    repo = RunTelemetryRepo()
    await repo.insert(
        db,
        _rec(
            media_kind="audio",
            tokens_in_video=0,
            tokens_in_audio=320,
            media_duration_secs=10.0,
        ),
    )
    ratios = await repo.recent_input_ratios(db, model="gemini-2.5-flash-lite", media_kind="audio")
    assert ratios == [32.0]


async def _insert_run(db, **over):
    """Insert a run_telemetry row with sensible defaults + caller overrides."""
    repo = RunTelemetryRepo()
    await repo.insert(db, _rec(**over))


@pytest.mark.asyncio
async def test_recent_output_rates_filtered_by_resolution(db):
    repo = RunTelemetryRepo()
    await _insert_run(db, model="m", media_kind="video", media_resolution_setting="high",
                      media_duration_secs=10.0, tokens_out=1000, tokens_thinking=0)
    await _insert_run(db, model="m", media_kind="video", media_resolution_setting="low",
                      media_duration_secs=10.0, tokens_out=100, tokens_thinking=0)
    high = await repo.recent_output_rates(db, model="m", media_kind="video", media_resolution="high")
    low = await repo.recent_output_rates(db, model="m", media_kind="video", media_resolution="low")
    assert high == [100.0]   # 1000/10
    assert low == [10.0]     # 100/10
    both = await repo.recent_output_rates(db, model="m", media_kind="video")  # no filter
    assert sorted(both) == [10.0, 100.0]


@pytest.mark.asyncio
async def test_recent_input_ratios_filtered_by_resolution(db):
    repo = RunTelemetryRepo()
    await _insert_run(db, model="m", media_kind="video", media_resolution_setting="high",
                      media_duration_secs=10.0, tokens_in_video=2000)
    await _insert_run(db, model="m", media_kind="video", media_resolution_setting="low",
                      media_duration_secs=10.0, tokens_in_video=500)
    assert await repo.recent_input_ratios(db, model="m", media_kind="video", media_resolution="high") == [200.0]
    assert await repo.recent_input_ratios(db, model="m", media_kind="video", media_resolution="low") == [50.0]


@pytest.mark.asyncio
async def test_est_cost_sums_by_job(db):
    repo = RunTelemetryRepo()
    await _insert_run(db, job_id=7, model="m", media_kind="video", status="ok",
                      cost_usd=0.20, est_cost_usd_p50=0.15)
    await _insert_run(db, job_id=7, model="m", media_kind="video", status="ok",
                      cost_usd=0.10, est_cost_usd_p50=0.05)
    sums = await repo.est_cost_sums_by_job(db, [7])
    assert sums[7] == pytest.approx(0.20)  # 0.15 + 0.05
