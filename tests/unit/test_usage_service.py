"""UsageService: month-window math, status thresholds, budget round-trip.

DB-only / offline-safe; ``now`` is injected so the month window is
deterministic (no inline wall clock).
"""

from datetime import datetime, timezone

import pytest

from backend.app.models.telemetry import RunTelemetryRecord
from backend.app.repositories.app_meta import AppMetaRepo
from backend.app.repositories.run_telemetry import RunTelemetryRepo
from backend.app.services.usage_service import (
    BUDGET_KEY,
    UsageService,
    month_start_utc,
)

NOW = datetime(2026, 6, 15, 10, 30, tzinfo=timezone.utc)


def _rec(**over) -> RunTelemetryRecord:
    base = dict(
        occurred_at="2026-06-07T12:00:00+00:00",
        install_id="inst-1",
        kind="studio",
        model="gemini-flash",
        status="ok",
        media_kind="video+audio",
        media_duration_secs=10.0,
        prompt_hash="h" * 64,
        tokens_in=3000,
        tokens_out=100,
    )
    base.update(over)
    return RunTelemetryRecord(**base)


def _service(db) -> UsageService:
    return UsageService(
        db_provider=lambda: db,
        run_telemetry_repo=RunTelemetryRepo(),
        app_meta_repo=AppMetaRepo(),
    )


async def _seed(db):
    repo = RunTelemetryRepo()
    # June 2026: priced rows summing to 10.00, plus an un-priced row.
    await repo.insert(db, _rec(occurred_at="2026-06-02T00:00:00+00:00", cost_usd=4.00))
    await repo.insert(db, _rec(occurred_at="2026-06-10T00:00:00+00:00",
                               model="gemini-pro", cost_usd=6.00))
    await repo.insert(db, _rec(occurred_at="2026-06-12T00:00:00+00:00", cost_usd=None))
    # May 2026: outside the window.
    await repo.insert(db, _rec(occurred_at="2026-05-30T00:00:00+00:00", cost_usd=99.0))


# ---- pure helper ----------------------------------------------------------


def test_month_start_utc_is_first_of_month_midnight():
    assert month_start_utc(NOW) == datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)


def test_month_start_utc_normalises_naive_to_utc():
    naive = datetime(2026, 6, 15, 10, 30)
    assert month_start_utc(naive) == datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)


# ---- current_month window + status ---------------------------------------


@pytest.mark.asyncio
async def test_current_month_window_excludes_other_months(db):
    await _seed(db)
    svc = _service(db)
    res = await svc.current_month(now=NOW)
    assert res["spend_usd"] == pytest.approx(10.00)  # June priced only
    assert res["priced_count"] == 2
    assert res["total_count"] == 3  # un-priced row counted in total
    assert res["period_start"] == "2026-06-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_status_none_when_no_budget(db):
    await _seed(db)
    svc = _service(db)
    res = await svc.current_month(now=NOW)
    assert res["budget_usd"] is None
    assert res["fraction"] is None
    assert res["status"] == "none"


@pytest.mark.asyncio
async def test_status_ok_warn_over_thresholds(db):
    await _seed(db)  # spend = 10.00
    svc = _service(db)

    await svc.set_budget(20.0)  # 50% → ok
    res = await svc.current_month(now=NOW)
    assert res["fraction"] == pytest.approx(0.5)
    assert res["status"] == "ok"

    await svc.set_budget(11.76)  # ~85% → warn
    res = await svc.current_month(now=NOW)
    assert res["status"] == "warn"

    await svc.set_budget(8.33)  # ~120% → over
    res = await svc.current_month(now=NOW)
    assert res["status"] == "over"


# ---- budget read / write -------------------------------------------------


@pytest.mark.asyncio
async def test_get_budget_absent_is_none(db):
    svc = _service(db)
    assert await svc.get_budget() is None


@pytest.mark.asyncio
async def test_set_get_budget_roundtrip(db):
    svc = _service(db)
    await svc.set_budget(25.0)
    assert await svc.get_budget() == pytest.approx(25.0)


@pytest.mark.asyncio
async def test_set_budget_none_clears_key(db):
    svc = _service(db)
    await svc.set_budget(25.0)
    await svc.set_budget(None)
    assert await svc.get_budget() is None
    # Cleared = key deleted.
    assert await AppMetaRepo().get(db, BUDGET_KEY) is None


@pytest.mark.asyncio
async def test_set_budget_zero_clears_key(db):
    svc = _service(db)
    await svc.set_budget(25.0)
    await svc.set_budget(0)
    assert await svc.get_budget() is None


@pytest.mark.asyncio
async def test_blank_or_garbage_budget_value_is_none(db):
    svc = _service(db)
    await AppMetaRepo().set(db, BUDGET_KEY, "  ")
    assert await svc.get_budget() is None
    await AppMetaRepo().set(db, BUDGET_KEY, "not-a-number")
    assert await svc.get_budget() is None


# ---- passthroughs --------------------------------------------------------


@pytest.mark.asyncio
async def test_by_model_and_by_day_scoped_to_month(db):
    await _seed(db)
    svc = _service(db)
    by_model = await svc.by_model(now=NOW)
    models = {r["model"]: r["cost_usd"] for r in by_model}
    assert models["gemini-pro"] == pytest.approx(6.00)
    assert models["gemini-flash"] == pytest.approx(4.00)

    by_day = await svc.by_day(now=NOW)
    days = {r["day"] for r in by_day}
    assert "2026-05-30" not in days  # May excluded
    assert "2026-06-02" in days
