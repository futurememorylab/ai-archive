# 0114. Annotate caching writes a prefetch-queue *visibility row*; resume via `x-init`, not `init()`

**Date:** 2026-06-23
**Status:** Accepted
**Lifespan:** Feature

## Context

Clicking **Annotate** caches the clip as its first step, but that caching ran
*inside the annotation job* via `proxy_resolver.path_for_clip_id(...)` — a path
entirely separate from the `prefetch_queue` the **Cache** button uses. So the
annotate-triggered download was invisible on the cache queue page, the annotate
button showed only a coarse `Caching` phase (no %), and a page reload mid-run
dropped all in-progress UI state. The `#78` work had already plumbed
`progress_cb` through the resolver and added `prefetch_queue.bytes_total` +
`update_progress`, so the progress infrastructure existed but wasn't wired into
the annotate path. See spec
`docs/specs/2026-06-23-annotate-cache-queue-consistency-design.md`.

## Alternatives

- **Route annotate caching through the `MediaPrefetcher` worker.** Fully unifies
  to one download path, but the worker is single-at-a-time by construction (the
  WireGuard pipe), so annotate would block behind unrelated queued downloads,
  and the annotator's `resolving → uploading → prompting` orchestration would
  have to be rewired around an external worker. Higher risk, slower for the
  user.
- **Add byte progress to the job SSE stream.** Would avoid a queue row but
  duplicates progress state, doesn't make the caching visible on the queue page,
  and grows the event schema.

## Decision

The annotator keeps its **own inline download** but, in the cache-miss branch,
records a `prefetch_queue` row purely for visibility/progress:

- New `PrefetchQueueRepo.start_inline()` inserts a row **born `downloading`**
  (so the worker's `claim_next`, which only takes `queued`, never
  double-fetches), reusing an existing active row idempotently.
- The shared `ProgressTracker` (renamed from `media_prefetcher._ProgressTracker`)
  is threaded into `path_for_clip_id`; the row is `mark_done` after the GCS
  upload, `mark_error` on failure. The row is byte-for-byte identical in shape
  to a Cache-button row, so the queue page renders it with no template change.
- Both the per-clip cache control and the annotate button read the percentage
  through one helper, `window.cacheProgressForClip()`, so the math always
  agrees.
- Resume after reload: `GET /api/jobs/active-for-clip/{clip_id}` reports the
  running job + item status; the annotate button reattaches the SSE stream.

**Resume hook is `_annotateInit()` called from the element's `x-init`, NOT an
`init()` method.** `clipAnnotate` is `Object.assign`-merged into the clip page's
x-data alongside `player()` and `reviewMixin()`, and Alpine honours a single
`init()` — `player` owns it. A second `init()` would silently clobber the
player's. This mirrors the existing `reviewMixin._reviewInit()` + `x-init`
pattern. Guarded by `tests/unit/test_annotate_progress_resume_wiring.py`.

## Consequences

- Annotate caching is now a first-class, visible, resumable cache operation,
  consistent with the Cache button across the queue page, the per-clip control,
  and reloads. Studio Run gets the same visibility (shares the cache-miss
  branch).
- A row authored by the annotator is not drained by the worker; on a crash
  mid-annotate `requeue_orphans` resets it to `queued` and the worker re-runs the
  idempotent `ensure_cached`. No double-download in the normal case.
- `run_job`/`_process_item` gained an optional `prefetch_queue_repo`; when absent
  (some unit tests) the row is simply skipped, so the caching still works.
- Test fakes for `path_for_clip_id` were aligned to the real protocol
  (`progress_cb=None`).

## Follow-up: startup orphan recovery for jobs

Verifying the resume feature surfaced a pre-existing gap: an annotation job runs
as a fire-and-forget background task, but a restart (deploy, crash, or the dev
`--reload` firing on a file save) kills it mid-flight while the DB still reads
`status='running'` / item `'prompting'`. Unlike the prefetch queue
(`requeue_orphans` on `MediaPrefetcher.start`), nothing recovered these — so the
clip page (and now the resume hook) kept showing "Annotating" forever for a dead
job. `JobsRepo.reset_transient()` existed for exactly this but was never wired
in.

`run_startup_cleanup` now runs job recovery at boot (single process → nothing is
in-flight by construction, same reasoning as the prefetcher):
`JobsRepo.cancel_orphaned_running` flips any still-`'running'` job **and its
unfinished items** to `'cancelled'`. Guarded by
`tests/integration/test_startup_orphan_job_recovery.py`. Studio-run orphans
(the `studio_runs` row staying `pending` after its job is cancelled) are a
separate, pre-existing issue and out of scope here.

The item must be cancelled too, not left `'pending'`: a terminal job with a
non-terminal item is internally inconsistent — the clip page (keyed on
`jobs.status='running'`) shows it stopped, but the Batches view counts a
`pending` item as `in_flight` and so showed the batch "Running". So `ItemStatus`
gained `'cancelled'`, the Batches aggregation counts cancelled items separately
(neither `in_flight` nor `completed`), and `batch_view` shows a neutral
**"Cancelled"** state for an interrupted job with no usable output — instead of
a phantom "Running" or a false green "Applied".

The annotate-button resume (`_annotateInit` → `/api/jobs/active-for-clip`) also
returns the job's `started_at` so the elapsed timer resumes from the true run
start; `elapsedTimer(start, offsetSeconds)` backdates it. Without this the clock
restarted at 0:00 on every page reload.
