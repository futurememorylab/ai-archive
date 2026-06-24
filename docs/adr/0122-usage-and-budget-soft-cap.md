# 0122. Usage & budget — a single monthly soft cap, spend surfaced everywhere

**Date:** 2026-06-24
**Status:** Accepted

## Context

Issue #30 ("Usage & budget") asks for four things: collect token cost/spend,
create a budget, an admin page for a spend overview, and an always-present
usage indicator. The cost-prediction epic (ADRs 0114–0119) already delivered
the first — every Gemini call writes a `run_telemetry` row with `cost_usd` and
`occurred_at`. This ADR covers the remaining three: a budget, the overview, and
the indicator. The open question was how strict the budget should be and where
its small amount of new state lives.

## Alternatives

- **Hard cap (block runs over budget).** Rejected: surprise-blocked work is
  worse than overspend for this team's workflow — a sweep or batch failing
  mid-way because a counter crossed a line is a footgun. The budget's job here
  is *awareness*, not enforcement.
- **Per-model / per-prompt budgets.** Deferred. One global monthly budget is
  the smallest thing that answers "are we on track this month?"; finer-grained
  budgets can layer on later without reworking this.
- **A dedicated `budget` table.** Rejected: the budget is a single scalar. The
  existing `app_meta` key/value table (migration 0016) holds it with no new
  migration.
- **Effective-dated budget history.** Rejected as over-engineering; the current
  budget is a single editable number. Past spend is immutable telemetry, so no
  history is lost.

## Decision

1. **Soft cap.** The budget is a single monthly USD amount in
   `app_meta['budget_monthly_usd']` (absent / `<= 0` ⇒ no budget; `set_budget`
   *deletes* the key to clear). It NEVER blocks a run — it only colours the
   indicator and adds an advisory warning on launch surfaces. The calibrate
   estimate response carries `would_exceed_budget`; the dialog appends
   "⚠ would exceed this month's budget" but the Launch button's
   `:disabled="!selCount()"` is unchanged (asserted by a test).

2. **`UsageService` on `CoreCtx`** (DB-only, offline-safe). `current_month(now)`
   returns spend, budget, fraction, and a `status` of `none` / `ok` (<0.8) /
   `warn` (0.8–1.0) / `over` (>1.0). The clock is **injected** (`now` param) so
   the month-window math is deterministic in tests. Spend = `SUM(cost_usd)` over
   `occurred_at` in the current UTC calendar month across **all** runs —
   annotation, studio, *and* calibration (calibration is real spend). New
   `run_telemetry` aggregates: `spend_in_period`, `spend_by_model_in_period`,
   `spend_by_day_in_period`, each a single GROUP BY (no N+1).

3. **Partial-pricing honesty.** Un-priced runs (NULL `cost_usd`, e.g. a model
   with no rate card) count in `total_count` but not `priced_count`; every spend
   surface shows "(N of M priced)" when they differ, so a partial sum is never
   read as the complete total — consistent with the rest of the epic.

4. **Admin "Usage" tab** (`/admin/usage`, CoreCtx): month spend vs budget with a
   status pill + a capped progress bar, by-model and by-day breakdowns, and a
   budget editor (`POST /admin/usage/budget`, 422 on a non-numeric/negative
   value, empty/0 clears).

5. **Always-present topbar pill.** Current-month spend is added to the existing
   in-memory `topbar_counts` (the async pre-render refresher from the topbar
   perf refactor) so full-page renders show it with zero sync I/O, plus a
   `/ui/usage-pill` HTMX poll (`load, every 60s`) mirroring the review-pill. The
   pill is `.pill` / `.pill.warn` / `.pill.bad` by status and always shows the
   month's spend (even with no budget). The read is guarded — a DB hiccup
   renders nothing rather than breaking the topbar.

## Consequences

- **Positive:** spend is visible at a glance on every page and in depth on the
  Usage tab; a budget gives an at-a-glance on-track signal and pre-spend warnings
  on the expensive surfaces — without ever blocking work. All DB-only, so it
  works offline. Delivers #30 in full.
- **Negative / accepted:** a single global monthly budget only — no per-model
  caps, no enforcement, no alerting beyond the in-app indicator. The soft cap
  can be ignored (by design). Hard caps / finer budgets are a future layer.
- **Period:** calendar month in UTC. Spend resets at the month boundary; there is
  no rollover or historical-month browser yet (the by-day breakdown covers the
  current month only).
