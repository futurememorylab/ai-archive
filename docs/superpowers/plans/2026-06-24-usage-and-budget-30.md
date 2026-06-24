# Usage & Budget (#30) — Implementation Plan

**Goal:** Deliver GitHub issue #30 "Usage & budget" in full, with a SOFT cap (warns, never blocks). Builds on the cost data already collected in `run_telemetry`.

#30 asks for four things; #1 is done, this plan does #2–4:
1. ✅ Collect token cost/spend — `run_telemetry.cost_usd` (+ `occurred_at`, `model`, `prompt_version_id`).
2. **Create a budget** — a single monthly USD budget (soft).
3. **Admin page for spent overview** — a "Usage" tab: current-month spend vs budget + breakdown.
4. **Usage indicator, always present** — a topbar pill showing current-month spend, coloured ok/warn/over.

**Soft cap:** the budget colours the indicator (ok < 80% / warn 80–100% / over > 100%) and shows a warning on launch surfaces when a projected run would exceed the remaining budget — but NEVER blocks a run.

**Storage:** budget lives in the existing `app_meta` key/value table (migration 0016) under key `budget_monthly_usd` (string float; absent = no budget set). No migration needed.

**Period:** calendar month in UTC, derived from `run_telemetry.occurred_at` (ISO text). "This month" = `occurred_at >= <first-of-month UTC>`.

**Spend includes ALL run_telemetry** (annotation + studio + calibration) — calibration is real spend. Mind NULL `cost_usd` (un-priced models): report priced-vs-total like the rest of the epic.

**Conventions:** CoreCtx / DB-only / offline-safe for everything here (no live services). Tests `.venv/bin/python -m pytest`. Commits SSH-signed. Branch `claude/relaxed-archimedes-ii1l2t`. Reuse design-language primitives (`.pill`, `ui.field`, `.admin-table`, `usd` filter) — the guard fails on hand-rolled classes.

---

## Task 2A — Spend data layer (foundation; do first)

**Files:** `backend/app/repositories/run_telemetry.py`, a new `backend/app/repositories/app_meta.py` (if none exists — check first), a new `backend/app/services/usage_service.py`, `backend/app/context.py` (wire UsageService onto CoreCtx), tests.

1. **run_telemetry spend aggregates** (all over `occurred_at >= start [AND < end]`, status-agnostic — spend is spend):
   - `spend_in_period(conn, *, start_iso, end_iso=None) -> {"cost_usd": float, "priced_count": int, "total_count": int}` — `COALESCE(SUM(cost_usd),0)`, `COUNT(cost_usd)`, `COUNT(*)`.
   - `spend_by_model_in_period(...) -> list[{"model": str, "cost_usd": float, "count": int}]` (GROUP BY model, ORDER BY cost DESC).
   - `spend_by_day_in_period(...) -> list[{"day": "YYYY-MM-DD", "cost_usd": float}]` (`GROUP BY substr(occurred_at,1,10)`).
   - Add an index on `occurred_at` if a perf test shows it's needed (table grows ~1KB/run; a month scan is fine for now — note it, don't prematurely index).
2. **app_meta get/set.** Check for an existing app_meta repo (`grep -rn "app_meta" backend/app/repositories backend/app/services`). If none, add `AppMetaRepo` with `async get(conn, key) -> str | None` and `async set(conn, key, value) -> None` (`INSERT … ON CONFLICT(key) DO UPDATE`). Keep it generic.
3. **UsageService** (`services/usage_service.py`, on CoreCtx — DB-only, offline-safe):
   - `async current_month() -> {"spend_usd": float, "budget_usd": float | None, "fraction": float | None, "status": "none"|"ok"|"warn"|"over", "priced_count": int, "total_count": int, "period_start": iso}`.
     - status: `none` if no budget set; else `ok` (<0.8), `warn` (0.8–1.0), `over` (>1.0). fraction = spend/budget.
   - `async by_model()` / `async by_day()` passthroughs for the admin page.
   - `async get_budget() -> float | None` / `async set_budget(usd: float | None)` (writes `app_meta['budget_monthly_usd']`; None/0 clears).
   - A pure helper for the month-start ISO (UTC) — **inject the current time** (do NOT call `datetime.now()` inline in a way that's untestable; accept a `now` param or a small clock seam) so tests are deterministic.
4. **Wire** UsageService onto `CoreCtx` (mirror how `enum_service`/`cache_inspector` are attached; cross-ref the CoreCtx⊆LiveCtx drift guard `tests/unit/test_context_delegation.py`).
5. **Tests:** seed run_telemetry rows across two months + priced/un-priced; assert period totals, by-model, by-day, the status thresholds (set budget → ok/warn/over), and that NULL costs are counted in `total_count` not `priced_count`. Deterministic `now`.
6. **Commit:** `feat(usage): spend aggregates + monthly budget store + UsageService`.

---

## Task 2B — Admin "Usage" tab (depends on 2A)

**Files:** `backend/app/routes/pages/admin.py` (+ `usage` view/route/budget-POST), new `backend/app/templates/pages/_admin_usage.html`, `backend/app/templates/pages/admin.html` (tab link), tests.

1. **GET `/admin/usage`** (`require_role('admin')`, CoreCtx): renders `_admin_usage.html` with `usage_service.current_month()` + `by_model()` + `by_day()`.
2. **Template** (reuse `.admin-table`, `.pill`, `.meta`, `usd` filter, `ui.field`):
   - A header: "This month: {{ spend | usd }}{% if budget %} / {{ budget | usd }}{% endif %}" + a status `.pill` (ok/warn/over) + a simple progress bar (reuse an existing bar pattern if one exists; else a minimal `<div>` with width % — no new `*-btn`/`*-menu`/`modal-*`).
   - When `priced_count < total_count`, the "(N of M priced)" partial note (consistent with the Prompts tab).
   - By-model table (model · cost · runs) and a by-day list/sparkline (keep simple — a list is fine).
   - A budget edit form: `{{ ui.field(...) }}` for monthly USD, posts to `/admin/usage/budget`. Empty/0 clears the budget.
3. **POST `/admin/usage/budget`** (`require_role('admin')`): validate a non-negative float (422 on bad input — try/except like the calibrate-estimate fix), `usage_service.set_budget(...)`, return the re-rendered usage partial (HTMX swap into `#admin-enum-region`, like the other admin tabs). Use `wireHtmx` semantics consistent with the other tabs (the fragment has its own root only if needed — match the models/prompts tab pattern).
4. **Tab link** in `admin.html` (after "Prompts").
5. **Tests:** GET renders spend + budget; POST sets/clears the budget and re-renders; bad budget → 422; offline-safe (no live ctx). Design-language + templates-shared guards green.
6. **Commit:** `feat(admin): Usage tab — monthly spend overview + budget editor`.

---

## Task 2C — Always-present usage pill + soft-cap warnings (depends on 2A)

**Files:** `backend/app/routes/...` (a `/ui/usage-pill` route — find where `/ui/review-pill` lives), new `backend/app/templates/pages/_usage_pill_inner.html`, `backend/app/templates/pages/_topbar_pills.html`, plus the soft-cap warning on the calibrate + batch projected-cost surfaces, tests.

1. **GET `/ui/usage-pill`** (CoreCtx, mirrors `/ui/review-pill`): renders `_usage_pill_inner.html` from `usage_service.current_month()`.
2. **Pill** in `_topbar_pills.html`: a STABLE container `<span id="usage-pill" hx-get="/ui/usage-pill" hx-trigger="load, every 60s" hx-target="#usage-pill" hx-swap="innerHTML">` (mirror the review-pill pattern exactly). The inner partial:
   - Always shows current-month spend: a `.pill` with `{{ spend | usd }}` (e.g. "$12.40").
   - Colour by status: `.pill.ok` (or default) / `.pill.warn`-equivalent / `.pill.bad` for over. Reuse existing `.pill.*` modifiers (warn → there's `.pill.warn` already; over → `.pill.bad`). Title shows "$X of $Y this month (NN%)" when a budget is set; just "$X this month" when not.
   - Offline/empty: if the DB read fails, render nothing or a neutral pill (never crash the topbar).
3. **Soft-cap warning on launch surfaces** (never blocks):
   - Calibrate dialog: when the projected sweep cost (already computed in `admin_calibrate_estimate`) + current-month spend would exceed the budget, the estimate line shows a muted warning ("⚠ would exceed this month's budget"). Add `remaining`/`over` info to the estimate response and surface it in `estimateLabel()`. Do NOT disable the Launch button.
   - Batch new-batch modal: same idea on its estimate line if cheap (reuse the usage_service); if it's heavy, scope to a follow-up and note it. Minimum bar: the calibrate surface warns.
4. **Tests:** `/ui/usage-pill` renders the spend + correct status class for ok/warn/over; the soft-cap warning appears in the calibrate estimate response when over, and the Launch button is NOT disabled (assert it stays enabled). Walkthrough: a topbar usage pill is present on a normal page.
5. **Commit:** `feat(usage): always-present topbar spend pill + soft-cap launch warnings`.

---

## Task 2D — ADR + walkthrough + regression

1. **ADR** `docs/adr/0122-usage-and-budget-soft-cap.md` (MADR-lite): the budget is a single monthly soft cap in `app_meta` (no new table); spend = SUM(cost_usd) over occurred_at incl. calibration; soft cap warns (indicator colour + launch note) but never blocks; UsageService is CoreCtx/offline-safe. Alternatives weighed: hard cap (rejected — surprise blocked work), per-model/per-prompt budgets (deferred — one global monthly budget first), new budget table (rejected — app_meta suffices). Update `docs/decisions.md`. Mark #30 addressed in the cost-prediction spec's "Consequences/foundation for #30" note.
2. **Walkthrough:** add/extend a scenario showing the Usage tab + the topbar pill (selector-validate; offline in-process app).
3. **Full regression:** `.venv/bin/python -m pytest tests/unit tests/integration -q` + `lint-imports` + the guards (design-language, templates-shared, context-delegation, htmx-alpine, no-x-data-stack). All green. Push.

---

## Self-review notes
- **Soft, never hard:** assert in 2C that the Launch/Start buttons stay ENABLED when over budget — the cap is advisory only.
- **Offline:** every usage surface is CoreCtx/DB-only; the pill + tab must render with `live_ctx=None`. The pill must never crash the topbar on a DB hiccup.
- **Partial pricing:** un-priced runs count in `total_count` not `priced_count`; surface "(N of M priced)" so the spend total isn't silently low — consistent with the rest of the epic.
- **Determinism:** inject `now` into UsageService month math (no inline wall-clock that breaks tests).
- **N+1 / perf:** the usage aggregates are single GROUP BY queries (not per-row); the pill polls every 60s (not per-render). Add an `occurred_at` index only if a perf test shows the month scan is slow.
- **Reuse:** topbar pill mirrors `/ui/review-pill`; admin tab mirrors the models/prompts tabs; no new class vocabulary.
