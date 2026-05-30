# 0042. Bulk "Annotate selected" — one job per media kind, ephemeral progress indicator

**Date:** 2026-05-30
**Status:** Accepted

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

- Several batch entries appear in the Batch filter for one user action
  (one per kind) — acceptable; each is independently reviewable/cancellable.
- Per-kind jobs run as concurrent asyncio tasks sharing the single
  aiosqlite connection; writes are serialized by aiosqlite. Acceptable at
  current scale.
- No DB migration required.
- **Known limitation:** because `/api/jobs/active` returns only running
  jobs and the indicator's `failed` flag resets on each page load, a
  *completed-with-errors* batch is only noticeable until the user
  navigates. While jobs are still running, failures-in-progress remain
  visible everywhere. Making the failed banner persist until explicitly
  dismissed across navigations is deferred (would need server-side
  unacknowledged-failure state to honor the no-client-storage non-goal).
