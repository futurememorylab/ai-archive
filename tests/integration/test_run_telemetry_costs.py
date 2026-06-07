"""RunTelemetryRepo cost readers — batched aggregates that power the
ACTUAL-cost UI surfaces (batches list, per-clip, per-annotation).

Both readers issue a single statement per chunk of job ids (no per-job
loop) — pinned by an assert_query_count test for 10 vs 50 ids.
"""

import pytest

from backend.app.models.telemetry import RunTelemetryRecord
from backend.app.repositories.run_telemetry import RunTelemetryRepo
from tests._helpers.query_count import assert_query_count


def _rec(**over) -> RunTelemetryRecord:
    base = dict(
        occurred_at="2026-06-07T12:00:00+00:00",
        install_id="inst-1",
        kind="annotation",
        model="gemini-2.5-flash-lite",
        status="ok",
        media_kind="video+audio",
        media_duration_secs=10.0,
        prompt_hash="h" * 64,
        tokens_in=3000,
        tokens_out=100,
        cost_usd=0.05,
    )
    base.update(over)
    return RunTelemetryRecord(**base)


@pytest.mark.asyncio
async def test_cost_sums_by_job_sums_per_job(db):
    repo = RunTelemetryRepo()
    # job 1: two ok rows → 0.05 + 0.07 = 0.12
    await repo.insert(db, _rec(job_id=1, clip_id=10, cost_usd=0.05))
    await repo.insert(db, _rec(job_id=1, clip_id=11, cost_usd=0.07))
    # job 2: one ok row
    await repo.insert(db, _rec(job_id=2, clip_id=20, cost_usd=0.30))
    sums = await repo.cost_sums_by_job(db, [1, 2])
    assert sums == {1: pytest.approx(0.12), 2: pytest.approx(0.30)}


@pytest.mark.asyncio
async def test_cost_sums_by_job_includes_error_and_null_rows(db):
    """Actual spend includes failed attempts (they still cost tokens) and
    treats a NULL cost as 0 — the sum must not vanish on one NULL row."""
    repo = RunTelemetryRepo()
    await repo.insert(db, _rec(job_id=5, clip_id=1, cost_usd=0.10))
    await repo.insert(db, _rec(job_id=5, clip_id=2, status="error", cost_usd=0.02))
    await repo.insert(db, _rec(job_id=5, clip_id=3, cost_usd=None))
    sums = await repo.cost_sums_by_job(db, [5])
    assert sums == {5: pytest.approx(0.12)}


@pytest.mark.asyncio
async def test_cost_sums_by_job_empty_input(db):
    repo = RunTelemetryRepo()
    assert await repo.cost_sums_by_job(db, []) == {}


@pytest.mark.asyncio
async def test_costs_for_jobs_keyed_by_job_and_clip(db):
    repo = RunTelemetryRepo()
    await repo.insert(db, _rec(job_id=1, clip_id=10, cost_usd=0.05))
    await repo.insert(db, _rec(job_id=1, clip_id=11, cost_usd=0.07))
    await repo.insert(db, _rec(job_id=2, clip_id=10, cost_usd=0.30))
    costs = await repo.costs_for_jobs(db, [1, 2])
    assert costs == {
        (1, 10): pytest.approx(0.05),
        (1, 11): pytest.approx(0.07),
        (2, 10): pytest.approx(0.30),
    }


@pytest.mark.asyncio
async def test_costs_for_jobs_sums_retries_per_clip(db):
    """A clip re-run inside the same job produces multiple rows; the
    per-clip cost is their sum (total spend on that clip)."""
    repo = RunTelemetryRepo()
    await repo.insert(db, _rec(job_id=1, clip_id=10, status="error", cost_usd=0.02))
    await repo.insert(db, _rec(job_id=1, clip_id=10, cost_usd=0.05))
    costs = await repo.costs_for_jobs(db, [1])
    assert costs == {(1, 10): pytest.approx(0.07)}


@pytest.mark.asyncio
async def test_costs_for_jobs_empty_input(db):
    repo = RunTelemetryRepo()
    assert await repo.costs_for_jobs(db, []) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("n", [10, 50])
async def test_cost_readers_constant_query_count(db, n):
    repo = RunTelemetryRepo()
    for jid in range(1, n + 1):
        await repo.insert(db, _rec(job_id=jid, clip_id=jid * 10, cost_usd=0.01))
    job_ids = list(range(1, n + 1))
    async with assert_query_count(db, 2) as counter:
        await repo.cost_sums_by_job(db, job_ids)
        await repo.costs_for_jobs(db, job_ids)
    assert counter.count == 2, f"[n={n}] expected 2 statements, got {counter.count}"
