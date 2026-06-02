# 0052. Batches hub — design calls

**Date:** 2026-06-02
**Status:** Accepted

## Context

The clips list already shows a per-job progress indicator, but once multiple
annotation jobs have been run — especially multi-prompt `run_group` batches —
there is no single view to see across all runs: which are complete, which have
failed clips, how many drafts are awaiting review. The spec (`docs/specs/
2026-06-02-batches-hub-design.md`) calls for a `/batches` hub page that groups
jobs by `run_group`, shows per-batch metrics, lets the operator drill into
failed clips, retry them, and hand off to the review flow.

Four non-obvious design calls were made during implementation; they are recorded
here.

## Alternatives

### 1. Batch grouping key and singleton representation

- **Option A (`run_group` only):** Only show multi-job batches (where
  `run_group IS NOT NULL`). Singleton jobs created without a group simply never
  appear. Rejected: a clips-list "Annotate all" with a single media kind
  creates a job without a `run_group`; those are the most common runs and would
  be invisible.
- **Option B (virtual key `COALESCE(run_group, 'job:'||id)`):** Every job
  appears: multi-job batches keyed by their `run_group` string, singletons
  keyed by their own id. Chosen: one query, all jobs visible, key is stable
  and unique.

### 2. Read path — DB-only vs live fetch

- **DB + live (cache-bypass):** Re-fetch clip metadata from CatDV to enrich
  batch rows. Rejected: violates the offline-safe contract (CatDV seat may be
  unavailable) and would slam the seat-limited server on every page load.
- **Pure DB (`get_core_ctx`):** All read queries go to the local SQLite store
  (`list_batches`, `count_total_batches`, `failed_items_for_jobs`). Clip names
  in failed-items rows come from `clip_cache`, which is already populated by
  the annotation worker. Chosen: page is fully usable offline; `CoreCtx` not
  `LiveCtx` makes the dependency explicit.

### 3. "+ New batch" UX — in-page picker vs. redirect

- **In-page clip picker:** Add a second search-and-pick modal to `/batches`
  so the operator never leaves the hub. Rejected: the clips list already
  provides that picker (select clips → Annotate selected → starts a job that
  feeds `/batches`). Adding a second renderer would violate the CLAUDE.md reuse
  rule and would drift from the canonical video-list/multi-select pattern.
  The design prototype's `clipData.js`/`clipList.js` modules were deliberately
  not ported for this reason.
- **Redirect to `/`:** The "+ New batch" button navigates to the clips list.
  Chosen: zero duplication, consistent UX, no new picker state machine.

### 4. Live refresh — SSE listener vs. polling vs. page reload

- **`location.reload()` on SSE message:** Simplest implementation; forbidden
  by CLAUDE.md and the `test_no_location_reload.py` check.
- **Per-page lifecycle hand-rolling (`Alpine.initTree` / `htmx.process`):**
  Already forbidden; those calls exist in exactly one file (`htmxAlpine.js`),
  enforced by `test_htmx_alpine_single_lifecycle.py`.
- **Polling interval:** Independent of the existing event bus; doubles the
  query load when a job is idle.
- **Piggyback the global `jobs` SSE topic via `htmxAlpine.reinit`:**
  `batchesPage` opens an `EventSource` on `/api/jobs/events` (the same topic
  every other page uses), debounces at 500 ms, fetches `/batches/table`, and
  re-inits the injected HTML via `window.htmxAlpine.reinit(region)`. Chosen:
  reuses the established event path, respects the single lifecycle helper, and
  produces no traffic when no job is running.

## Decision

- **Batch key:** `COALESCE(run_group, 'job:'||id)` — every job is visible; the
  aggregation is a single grouped SQL query (ADR 0046: no N+1).
- **Read path:** `get_core_ctx` only. Three `JobsRepo` methods
  (`list_batches`, `count_total_batches`, `failed_items_for_jobs`) issue one
  SQL statement each; the `/batches` read path is always exactly three
  statements regardless of batch count (pinned by
  `tests/integration/test_batches_page_perf.py`).
- **Retry path:** `get_live_ctx` (typed 503 when offline) + a proxy-resolver
  guard. Retry calls `annotator.run_job(..., only_clip_ids=...)`, which already
  re-runs only `error`/`pending` items, narrowed further to the requested clip
  set when `only_clip_ids` is provided.
- **"+ New batch":** Redirects to the clips list (`href='/'`). No second picker.
  The design prototype's replica JS modules were not ported.
- **Live refresh:** `batchesPage()` Alpine controller listens on
  `/api/jobs/events` SSE, debounces 500 ms, fetches `/batches/table`, and
  calls `window.htmxAlpine.reinit(region)` — the single permitted lifecycle
  entry point.

## Consequences

- The `/batches` page is fully usable when CatDV is offline; it degrades
  gracefully (failed-clip names may show "Clip N" if the clip was never cached,
  but counts and all controls remain functional).
- Retry correctly re-runs only the failed clips (not the whole job), using
  the same `run_job` code path already tested by `test_annotator_worker.py`.
- The N+1 guard in `test_batches_page_perf.py` pins the three-statement read
  contract; any regression to a per-batch loop will break CI.
- Live refresh never calls `location.reload()` or hand-rolls `Alpine.initTree`;
  both are enforced by existing guardrail tests.
- The clips-list picker is the only clip-selection surface; the batches hub is
  read-only by design (except for retry). Future "re-run with a different
  prompt" capability would be a new route, not an in-page second picker.
