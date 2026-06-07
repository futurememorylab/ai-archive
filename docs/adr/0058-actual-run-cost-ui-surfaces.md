# 0058. Actual run cost on the UI — "total spend" semantics + a shared `usd` filter

**Date:** 2026-06-07
**Status:** Accepted

## Context

ADR 0057 landed per-run cost capture: every Gemini call writes a
`run_telemetry` row with `cost_usd` (+ `job_id`, `clip_id`, `kind`,
`status`), and `studio_run.cost_usd` now holds the real billable cost.
The pre-run *estimate* already renders. This task surfaces the *actuals*
on four read surfaces — the batches list (per batch), the batch-filtered
clips list (per clip), the studio run output panel (per run), and the
clip-detail published panel (per annotation) — and fixes the new-batch
modal's misaligned estimate line.

Two design questions had to be answered consistently across surfaces:
(1) what does "the cost of a batch / clip / annotation" mean when a clip
was retried or a call failed, and (2) how is a money value formatted in
server-rendered HTML (JS already has `fmtUsd`).

## Alternatives and Decisions

### 1. Cost = total billable spend (include error rows; sum retries)

A clip can be processed more than once: a failed attempt still burns
tokens, and a retry adds a second `run_telemetry` row. The displayed
number could mean "cost of the successful run" or "total spent on this
clip/batch".

**Alternative considered:** Show only `status='ok'` rows (the estimator
deliberately excludes error / MAX_TOKENS rows so bad data doesn't poison
*forecasts*). Rejected for *display*: the user is looking at money already
spent, and a failed attempt cost real money. Hiding it would understate
the bill and make "estimate vs actual" comparisons misleading.

**Decision:** All cost readers (`RunTelemetryRepo.cost_sums_by_job`,
`costs_for_jobs`) sum `COALESCE(cost_usd, 0)` over **every** row for the
job(s) — error rows included, retries summed. This is the opposite of the
estimator's filtering, and intentionally so: estimation wants clean
signal, accounting wants the true total.

### 2. Two batched readers, mirroring `failed_items_for_jobs`

The four surfaces need cost keyed three ways: per batch (sum of its job
ids), per clip within a batch, and per annotation (one job). Rather than
one reader per surface, two cover all four:

- `cost_sums_by_job(job_ids) -> {job_id: total}` — batches list sums a
  batch's member job ids in Python.
- `costs_for_jobs(job_ids) -> {(job_id, clip_id): total}` — per-clip and
  per-annotation surfaces.

Both follow the single-column `IN (?, …)` shape of
`JobsRepo.failed_items_for_jobs` (one statement per call, job-id list
bounded by the page's batch/clip limit), not `chunked_in_clause` — that
helper is for two-column tuple keys. The N+1 pins
(`test_batches_page_perf.py`, `test_clips_page_perf.py`) cover both, and a
dedicated `assert_query_count` test pins the readers at a constant 2
statements for 10 vs 50 job ids (ADR 0046).

### 3. One server-side `usd` filter mirroring `fmtUsd`

JS contexts format money via `static/format.js::fmtUsd`. Server-rendered
surfaces (batches list, per-clip, studio panel, clip detail) needed the
same. Hand-rolling `${{ "%.4f"|format(x) }}` per template (as the studio
panel previously did) drifts from the JS rules and from each other.

**Decision:** A single `usd` filter on the one shared Jinja env
(`routes/pages/templates.py`, alongside `bytes_human` / `comma`), with
`fmtUsd`'s exact semantics: `None → "—"`; `< $0.10 → 3 decimals` (small
per-clip costs need the precision); otherwise 2 decimals; always a `$`
prefix. The pre-existing studio-panel `%.4f` hand-roll was switched to
`|usd`. Per-clip and per-annotation costs commonly fall under a dime, so
the 3-decimal branch is the common case there.

### 4. Where cost lands per surface

- **Batches list** — a `.muted` sub-line under the Status pill
  (`_batches_table.html`). `None` (no telemetry yet) renders nothing, not
  `$0.00`, so old batches predating telemetry aren't mislabelled as free.
- **Batch-filtered clips list** — a `.muted` sub-line in the batch cell
  (`_clips_row_cells.html`); only computed when a batch filter is active,
  so the unfiltered clips-list query count is unchanged.
- **Studio run output** — folded into the existing `.run-stats` line
  (`_studio_run_output.html`), replacing the hand-rolled format.
- **Clip detail** — a "Run cost" `.muted` line at the top of the
  *published* panel (`clip_detail.html`), sourced from the latest
  annotation that carries a `job_id`. Live/manual annotations have no
  `job_id` (nothing billed) and show nothing.

### 5. Estimate-line alignment in the new-batch modal

The estimate sat between a `.grow` spacer and the primary button inside
`.modal-actions` (a `flex; justify-content:flex-end` row with default
`align-items:stretch`), so the block-level `<div>` stretched full height
and its text floated above the buttons' centred baseline.

**Decision:** `.modal-actions` gets `align-items:center`; the estimate
becomes `.nb-estimate` with `margin-right:auto` so Cancel sits left, the
estimate fills the middle (left-aligned, vertically centred), and the
primary button is pushed to the far right. The `.grow` span and inline
`text-align:right` are removed. Matches the row's existing flex idiom
rather than moving the estimate to its own line.

## Consequences

- Displayed cost is *total spend*, deliberately inconsistent with the
  estimator's clean-signal filtering — documented here so a future reader
  doesn't "fix" the readers to match the estimator.
- `usd` is the canonical server-side money formatter; new surfaces use
  `|usd`, never a hand-rolled `%`-format. It is unit-pinned against
  `fmtUsd`'s rules in `test_templates_shared.py`.
- The batches N+1 pin moved from 3 to 4 statements
  (`cost_sums_by_job` is the 4th); the clips-list pin is unchanged
  because the per-clip cost query only fires when a batch filter is set.
- `None` cost renders as nothing on every surface, so batches/clips
  predating telemetry are not mislabelled `$0.00`.
