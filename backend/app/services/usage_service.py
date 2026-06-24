"""UsageService — current-month spend vs a single monthly USD soft budget.

Lives on CoreCtx: DB-only, offline-safe (every method is a SQLite read or
write, no live services). Spend = SUM(run_telemetry.cost_usd) over
occurred_at, including ALL run kinds (annotation + studio + calibration —
calibration is real spend). The budget is a single soft cap stored in
``app_meta['budget_monthly_usd']`` (absent / cleared = no budget).

Determinism: the month-window methods take an injected ``now`` (a datetime)
— callers pass the wall clock, this module never reads it inline, so tests
pin the window. ``month_start_utc`` is a pure helper.

Soft cap: ``current_month`` returns a ``status`` (none/ok/warn/over) used to
colour the indicator and warn on launch surfaces. It never blocks a run.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

import aiosqlite

from backend.app.repositories.app_meta import AppMetaRepo
from backend.app.repositories.run_telemetry import RunTelemetryRepo

BUDGET_KEY = "budget_monthly_usd"

# Soft-cap thresholds: fraction of budget spent.
_WARN_FRACTION = 0.8


def month_start_utc(now: datetime) -> datetime:
    """First instant of ``now``'s calendar month, in UTC. Pure/testable.

    ``now`` is normalised to UTC first (a naive datetime is assumed UTC), so
    the window boundary matches the UTC ISO timestamps stored on run_telemetry.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    return now.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )


def _status_for(fraction: float | None) -> str:
    if fraction is None:
        return "none"
    if fraction >= 1.0:  # spend == budget is already over (fully consumed), not "nearing"
        return "over"
    if fraction >= _WARN_FRACTION:
        return "warn"
    return "ok"


class UsageService:
    def __init__(
        self,
        *,
        db_provider: Callable[[], aiosqlite.Connection],
        run_telemetry_repo: RunTelemetryRepo,
        app_meta_repo: AppMetaRepo,
    ) -> None:
        self._db = db_provider
        self._telemetry = run_telemetry_repo
        self._meta = app_meta_repo

    # ---- budget read / write -----------------------------------------
    async def get_budget(self) -> float | None:
        """The monthly budget in USD, or None if unset / blank / unparsable."""
        raw = await self._meta.get(self._db(), BUDGET_KEY)
        if raw is None or not raw.strip():
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    async def set_budget(self, usd: float | None) -> None:
        """Set or clear the monthly budget.

        Clear semantics: None or a value <= 0 DELETES the key — "no budget set"
        is the absence of the key, so ``get_budget`` returns None and
        ``current_month`` reports status 'none'. A positive value is stored as
        its string form.
        """
        if usd is None or usd <= 0:
            await self._meta.delete(self._db(), BUDGET_KEY)
            return
        await self._meta.set(self._db(), BUDGET_KEY, str(float(usd)))

    # ---- month window helpers ----------------------------------------
    @staticmethod
    def _period_start_iso(now: datetime) -> str:
        return month_start_utc(now).isoformat()

    # ---- current-month summary ---------------------------------------
    async def current_month(self, *, now: datetime) -> dict:
        """Spend vs budget for ``now``'s calendar month (UTC).

        Returns spend_usd, budget_usd (or None), fraction (or None),
        status (none/ok/warn/over), priced_count, total_count, period_start.
        """
        start_iso = self._period_start_iso(now)
        spend = await self._telemetry.spend_in_period(self._db(), start_iso=start_iso)
        budget = await self.get_budget()
        fraction: float | None
        if budget is None or budget <= 0:
            fraction = None
        else:
            fraction = spend["cost_usd"] / budget
        return {
            "spend_usd": spend["cost_usd"],
            "budget_usd": budget,
            "fraction": fraction,
            "status": _status_for(fraction),
            "priced_count": spend["priced_count"],
            "total_count": spend["total_count"],
            "period_start": start_iso,
        }

    async def by_model(self, *, now: datetime) -> list[dict]:
        """By-model spend breakdown for the current month."""
        start_iso = self._period_start_iso(now)
        return await self._telemetry.spend_by_model_in_period(
            self._db(), start_iso=start_iso
        )

    async def by_day(self, *, now: datetime) -> list[dict]:
        """Daily spend series for the current month."""
        start_iso = self._period_start_iso(now)
        return await self._telemetry.spend_by_day_in_period(
            self._db(), start_iso=start_iso
        )
