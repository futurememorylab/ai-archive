# 0045. Bulk "Annotate selected" — one job per media kind, ephemeral progress indicator

**Date:** 2026-05-30
**Status:** Accepted
**Lifespan:** Feature

## Context

The clips list had no way to run annotations across many clips; only the
single-clip Annotate dropdown existed. A selection is often mixed media
kinds, and a prompt's `media_kind` is `video`/`image`/`any` — one prompt
can't serve every clip. We also wanted progress visible while the user
keeps working (e.g. reviewing earlier drafts).

## Alternatives

1. Extend the `jobs` schema to carry multiple prompts (one per kind) in a
   single job.
2. Create one job per assigned media kind, reusing the existing
   single-prompt job model unchanged.

For progress visibility:

A. Ephemeral top-bar indicator: re-derive state from `GET /api/jobs/active`
   (running jobs only) on each page load + live `GET /api/jobs/events`
   (global `jobs` SSE topic). No client storage, server is source of truth.
B. Persist a "failed until dismissed" banner across navigations, which
   requires either client storage (rejected as a non-goal) or server-side
   unacknowledged-failure tracking (new state + ack endpoint).

## Decision

- **One job per media kind** (alternative 2). `POST /api/jobs` + `run_job`
  already handle a single prompt over many clips, so per-kind jobs reuse
  all of it with zero schema change. The picker groups the selection by
  each row's kind and POSTs one job per assigned kind; kinds with no
  assigned prompt are skipped (surfaced before Run).
- **Ephemeral indicator** (alternative A). The topbar component aggregates
  progress across active jobs via the global `jobs` topic and
  `/api/jobs/active`. It self-starts on every page (`init()` opens one
  EventSource per page load).

## Consequences

- One bulk action still creates one job per kind, but the per-kind jobs
  share a `run_group` token (jobs migration 0015) so the Batch filter and
  the indicator present them as a single run: the dropdown collapses them
  into one entry whose value is all their job ids, and `batch=` accepts a
  comma-separated id list resolving to the union of their clips.
- Per-kind jobs run as concurrent asyncio tasks sharing the single
  aiosqlite connection; writes are serialized by aiosqlite. Acceptable at
  current scale.
- No DB migration required.
- **Studio jobs are excluded from the global indicator.** Prompt Studio
  runs go through the same `run_job` with `kind="studio"` and have their
  own progress UI. The three global-topic publishes in `run_job` are
  guarded by `kind != "studio"`, and `JobsRepo.list_running` filters
  `COALESCE(kind,'') != 'studio'`, so only `kind IS NULL` annotation jobs
  surface in the topbar.
- **Offline kickoff surfaces a message rather than a silent no-op.**
  `POST /api/jobs` returns `started: false` when `auto_start` can't fire
  (annotation services unavailable); the picker collects these and shows
  an error, keeping the modal open instead of closing as if jobs ran.

## Post-QA fixes

Manual QA surfaced four issues; the notable design calls:

- **`batch=` now resolves to every clip in the job** (`job_items`), not
  just clips with pending review drafts. The indicator is only visible
  while a job runs — when no drafts exist yet — so the old "pending-only"
  resolution made the Batch view empty mid-run. The clips list now shows a
  per-clip run-status pill (Queued / Processing / Done / Applied / Failed)
  derived from `job_items.status`. This also changes the existing Batch
  dropdown to the same "all clips in the run" meaning.
- **Filtered/batch views hydrate from the local list cache**
  (`ClipListCacheRepo.clips_for_catalog`) before any per-clip
  `archive.get_clip`. The previous per-clip CatDV round-trip made the Batch
  view slow; parallelizing those fetches was rejected (it would hammer the
  seat-limited CatDV server, violating the session discipline).
- **Gemini calls run off the event loop.** `gemini.annotate()` is a
  synchronous Vertex AI call; it now runs via `asyncio.to_thread`, so a
  running batch no longer freezes page requests. (Pre-existing blocking,
  but bulk runs made it continuous.)
- **Known limitation:** because `/api/jobs/active` returns only running
  jobs and the indicator's `failed` flag resets on each page load, a
  *completed-with-errors* batch is only noticeable until the user
  navigates. While jobs are still running, failures-in-progress remain
  visible everywhere. Making the failed banner persist until explicitly
  dismissed across navigations is deferred (would need server-side
  unacknowledged-failure state to honor the no-client-storage non-goal).
