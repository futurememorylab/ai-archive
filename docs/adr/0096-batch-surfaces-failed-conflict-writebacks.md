# 0096. Batches surface failed/conflict write-backs as a problem state

**Date:** 2026-06-17
**Status:** Accepted

## Context

ADR 0095 made the batch status honest about *in-flight* write-backs ("Syncing N",
sourced from `pending_operations` so it can't contradict the topbar sync chip).
But it only accounted for `pending`/`in_flight` ops. A write-back that **failed**
(exhausted its retry ceiling, ADR 0091) or hit a **conflict** (the clip changed
upstream after review) leaves a `pending_operations` row in a terminal-bad state
that the SyncEngine will not retry on its own. Such a batch had no in-flight ops
and no awaiting reviews, so `batch_view` fell through to green **"Applied"** —
hiding the fact that the change never reached CatDV. The topbar sync chip already
counts these as `problems` (`count_actionable`), so the chip and the batch row
disagreed.

## Decision

- **`list_batches` counts problem clips.** A `problems` CTE counts the batch's
  distinct clips with a `pending_operations` row in `('failed','conflict')`,
  joined the same way as the `syncing` CTE (`job_items.catdv_clip_id` ↔
  `pending_operations.provider_clip_id`). Exposed as `problem_clips`, mirroring
  `syncing_clips`. Same source as the sync chip, so they can never disagree.
- **`batch_view` precedence:** running → awaiting-review → **problem** → syncing →
  applied. Problems beat both syncing and applied so a stuck queue is never masked
  green, but stay below awaiting-review (active human work comes first). The
  problem state renders `status_state="bad"` with label `"{n} failed to sync"`,
  reusing the existing `.pill.bad` styling — no template change. Both
  `syncing_clips` and `problem_clips` are read with `.get(..., 0)` defaults so
  older callers/tests keep working.

## Consequences

- A batch whose write-back is stuck now reads red "N failed to sync" and the
  operator can retry it from the Sync drawer — consistent with the topbar chip.
- Refines ADR 0095 (which added `syncing_clips`); the two counts are disjoint
  (`pending`/`in_flight` vs `failed`/`conflict`).
- Repo-level coverage in `tests/integration/test_jobs_repo_batches.py`; pure
  `batch_view` precedence coverage in `tests/unit/test_batch_view.py`.
