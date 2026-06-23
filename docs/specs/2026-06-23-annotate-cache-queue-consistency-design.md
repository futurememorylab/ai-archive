# Annotate-cache queue consistency & in-progress resume

**Date:** 2026-06-23
**Status:** Accepted
**Issue:** #78 (testing-findings follow-up)

## Context

When a user clicks **Annotate** on a clip, the first step is to cache the
clip's media (download the proxy from CatDV, upload it to the AI store). Today
that caching runs *inside the annotation job* via
`proxy_resolver.path_for_clip_id(...)` — a code path completely separate from
the `prefetch_queue` that the **Cache** button uses.

Three user-visible inconsistencies result:

1. **Invisible in the cache queue.** The annotate-triggered download never
   creates a `prefetch_queue` row, so the queue page (which reads
   `/api/cache/prefetch/queue`) doesn't show it. When another clip is already
   downloading, the user can't see "their" clip anywhere.
2. **No progress in the button.** The annotate button narrates only the coarse
   phase (`Caching` / `Annotating`) + an elapsed timer. There is no byte-level
   percentage like the Cache button shows, because the job's SSE stream only
   emits coarse statuses (`resolving` → `uploading` → `prompting`).
3. **Lost on reload.** Reloading the clip page (or navigating away and back)
   mid-annotate drops all in-progress UI state: the button returns to idle even
   though the job keeps running server-side.

The `#78` work already plumbed `progress_cb` through the resolver + media-cache
backend and added `prefetch_queue.bytes_total` + `update_progress`, so the
infrastructure to report download progress exists; it just isn't wired into the
annotate path.

## Alternatives

**A. Visibility row (chosen).** The annotator keeps its own inline download but
writes a `prefetch_queue` row and reports byte progress through the existing
`progress_cb`. The row appears in the queue with a live %, and the button reads
the % from the queue the same way the Cache button does. Preserves the current
parallel-download behaviour (annotate is not blocked behind the single-worker
prefetcher); lowest risk.

**B. Route through the worker.** Annotate enqueues a prefetch and *waits* for
the single-at-a-time `MediaPrefetcher` to download it, then annotates. Fully
unified to one download path, but annotate would now block behind any unrelated
queued download (the WireGuard one-at-a-time constraint), and the annotator's
`resolving → uploading → prompting` phase orchestration would have to be
rewired around an external worker. Rejected as higher-risk and slower for the
user.

## Decision

Implement **Alternative A**. The annotate caching becomes *visible and
progress-reporting* without changing who performs the download.

### 1. Backend — annotator writes a visibility row

In `annotator._process_item`, the cache-miss branch (`upload is None`):

- New repo method
  `PrefetchQueueRepo.start_inline(conn, *, key, who="annotate") -> int`:
  reuse an existing active row for the clip if one exists (idempotent, like
  `enqueue`), otherwise **insert a row directly as `status='downloading'`**
  with `started_at` set. Inserting as `downloading` (not `queued`) means the
  prefetcher's `claim_next` — which only takes `queued` rows — never
  double-fetches the clip.
- Wrap the existing resolve→upload steps:
  1. `rid = start_inline(...)`
  2. `tracker = ProgressTracker(queue_repo, conn=db, rid=rid)`
  3. `path_for_clip_id(clip, progress_cb=tracker)` (download — progress fires here)
  4. `ensure_uploaded(...)` (GCS upload — no progress, same as the Cache button)
  5. `mark_done(rid, bytes_downloaded=tracker.last_downloaded)`
  6. on `ProxyNotFound` / any failure in this span: `mark_error(rid, humanise(exc))`.
- Reuse the prefetcher's progress tracker: rename `media_prefetcher._ProgressTracker`
  → public `ProgressTracker` (update its one test and the prefetcher call
  site), import it into the annotator. No new throttling code.

The row's lifecycle (`downloading` → `done`/`error`, progress on download) is
byte-for-byte identical to a Cache-button row, so the queue page renders it with
no template changes.

### 2. Backend — resume lookup

- `JobsRepo.find_running_item_for_clip(conn, clip_id) -> dict | None` returning
  `{"job_id": int, "item_status": str}` for a `job_items` row whose
  `catdv_clip_id` matches and whose parent `jobs.status = 'running'` (most
  recent if several). `None` when no running job touches the clip.
- `GET /api/jobs/active-for-clip/{clip_id}` returns that dict, or `{}` when
  `None`. Read-only, `CoreCtx`.

### 3. Frontend — % in the annotate button

- New shared helper in `static/format.js`:
  `window.cacheProgressForClip(clipId) -> Promise<{status, pct}|null>` —
  fetches `/api/cache/prefetch/queue`, finds the clip's active row, and returns
  the percentage using the same `floor(100 * downloaded / total)` math the
  Cache button already uses (centralised so both surfaces agree; `pct` is
  `null` when total is unknown).
- `clipAnnotate` gains a reactive `cachePct` and a light poll (~1.2 s) that runs
  **only while `phase === 'caching'`**, setting `cachePct` from the helper and
  stopping at the caching→annotating handoff / run end.
- `_annotate_dropdown.html`: the running label becomes
  `Caching` + (`cachePct` present ? ` <pct>%` : `''`); `Annotating` and the
  elapsed timer are unchanged.

### 4. Frontend — full button resume on reload

- `clipAnnotate.init()` calls `/api/jobs/active-for-clip/{id}`. If a running job
  is found, `_resumeRun(jobId, itemStatus)`:
  - sets `running = true`, `scope = 'draft'`, `jobId`;
  - seeds `phase` from `CA_PHASE[itemStatus]`;
  - restarts the elapsed timer (from now — original start time is not
    persisted);
  - starts the cache-% poll if still in the caching phase;
  - reattaches the SSE stream via `attachStream(jobId)` so the remaining phases
    narrate and the draft loads on `review_ready`.
  - Suppresses toasts for already-passed phases (no prompt name is available on
    resume), by pre-setting the `_announced*` flags.
- The cache **badge** resume + queue-row resume shipped in the previous turn
  (cacheActions `init()` + the `#prefetch-panel.htmx-request` flicker fix) still
  cover the badge surface.

## Consequences

- Annotate caching is now a first-class, visible, resumable cache operation —
  consistent with the Cache button across the queue page, the per-clip control,
  and reloads.
- A `prefetch_queue` row authored by the annotator is **not** drained by the
  worker (it is born `downloading`); if the process crashes mid-annotate,
  `requeue_orphans` resets it to `queued` and the worker will re-run
  `ensure_cached`, which is idempotent (`status()` short-circuits). No
  double-download in the normal case.
- Edge case: if the user clicks **Cache** and **Annotate** on the same clip
  concurrently, `start_inline` reuses the existing active row, so both surfaces
  track one row. Acceptable; not the common path.
- The progress tracker is now shared (`ProgressTracker`), tightening the
  contract that both cache paths report progress identically.

## Manual acceptance flows

1. **Annotate shows in the queue (visibility).** On a clip whose media is *not*
   cached, open `/clips/{id}`, open the cache queue page in a second tab. Click
   **Annotate → pick a prompt**. *Expected:* within ~2 s a row for this clip
   appears in the active queue on the queue page with status `downloading` and a
   climbing `NN%  X.X MB`, then flips to the Recent list as `done`.
2. **Progress in the annotate button.** During flow 1, watch the Annotate
   button. *Expected:* it reads `Caching NN%` with the percentage climbing
   (matching the queue row), then switches to `Annotating`, then completes and
   loads the draft. (For a clip whose proxy size is unknown it shows plain
   `Caching` — no regression.)
3. **Resume on reload (caching phase).** Start an annotate on a large uncached
   clip; while the button reads `Caching NN%`, reload the page. *Expected:* the
   button comes back showing `Caching` (and a % once the next poll lands), the
   spinner is active, and when caching finishes it proceeds to `Annotating` and
   loads the draft — without a second click.
4. **Resume on reload (annotating phase).** Same as flow 3 but reload while the
   button reads `Annotating`. *Expected:* the button resumes in `Annotating`
   and loads the draft on completion.
5. **Already-cached clip is unaffected.** On a clip already in the AI store,
   click **Annotate**. *Expected:* no queue row, no `Caching` label — it goes
   straight to `Annotating` (existing fast-path behaviour preserved).
6. **Cache button still works.** Click **Cache** on an uncached clip.
   *Expected:* unchanged — queue row with %, badge spinner with %, button flips
   to **Purge**; no flicker on the queue table (previous-turn fix intact).
