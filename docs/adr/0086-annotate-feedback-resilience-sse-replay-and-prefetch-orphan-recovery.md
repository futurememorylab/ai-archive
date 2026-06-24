# 0086. Annotate-feedback resilience: SSE replay-on-connect + prefetch orphan recovery

**Date:** 2026-06-15
**Status:** Accepted
**Lifespan:** Invariant

## Context

Issue #58 added cache-then-annotate feedback to the clip page: the run
button narrates `Caching {t}` → `Annotating {t}`, toasts fire in phase
order, and the cache badge mirrors the upload. The UI is driven entirely
off the job's per-item status stream (`resolving` / `uploading` /
`prompting` / `review_ready`) published on the in-process `EventBus` and
relayed over SSE (`GET /api/jobs/{id}/events`).

Wiring the feedback surfaced two latent bugs that share one root cause:
**authoritative state lives in the DB (the `job_items.status` row, the
`prefetch_queue.status` row), but the in-memory layer that the consumer
reads drops that state across a lifecycle/timing gap.** Neither was
introduced by #58 — the feature just made them visible because it was the
first surface to *depend* on every phase event arriving.

1. **Subscribe-after-publish race (SSE).** `pick()` POSTs the job with
   `auto_start: true`, so the annotator starts and emits `resolving` /
   `uploading` within milliseconds — but the browser's `EventSource` is a
   *separate* HTTP request that subscribes ~0.5–1 s later. `EventBus`
   delivers only to subscribers present at publish time (no replay), so the
   early frames were gone before the client connected. The "Caching…"
   feedback never showed even though the DB proved the upload happened
   during that job. The slow Gemini phase was always caught, so annotation
   "just ran" with no caching signal.

2. **Orphaned in-flight prefetch rows.** A row left `downloading` by a
   process that died mid-download (crash / SIGKILL / the disk-full incident)
   was never re-claimed — `claim_next` only takes `queued` — and `enqueue`
   de-duped new requests onto the dead row. The cache spinner polled a row
   that no worker owned, forever.

Both are "the DB knows the truth; the in-memory layer forgot it."

## Alternatives

- **Buffer the last-N events per topic in `EventBus`** so a late subscriber
  gets a replay. Rejected: turns the bus into a stateful ring buffer with
  per-topic GC and a tuning knob, for a problem the DB already answers
  authoritatively. The status *is* persisted; re-reading it is simpler than
  caching it twice.
- **Subscribe before starting the job** (POST without `auto_start`, attach
  SSE, then start). Rejected: `auto_start` is server-side; coordinating a
  client-driven start round-trip is more moving parts and still races on
  reconnect.
- **Heartbeat / lease on `downloading` rows** so a stale lease is reclaimed
  after a timeout. Rejected as overkill for a single-worker queue: by
  construction nothing is in-flight at boot, so "downloading at start" is an
  unambiguous orphan — no timing threshold needed.
- **Have the frontend poll job status instead of SSE.** Rejected: SSE is the
  low-latency path and already exists; polling is the *fallback* (`pollJob`
  on `es.onerror`), not the primary.

## Decision

Re-read the persisted truth at the moment the in-memory layer would
otherwise be empty.

- **SSE replay-on-connect.** `job_events` now subscribes to the bus
  **first**, then emits one synthetic frame per non-`pending` item from
  `jobs_repo.list_items` (`_replay_frames`), then relays live events.
  Subscribe-before-read closes the pre-subscribe race; the queue closes the
  post-read one. A client joining mid-upload gets the current `uploading`
  phase; one joining after completion gets `review_ready` and finishes. The
  replayed frames reuse the live payload shape (`{item_id, status, …}`), so
  the frontend handlers are unchanged and idempotent on duplicates.

- **Prefetch orphan recovery.** `PrefetchQueueRepo.requeue_orphans` flips
  every `downloading` row back to `queued`; `MediaPrefetcher.start()` calls
  it once before launching the drain loop. Safe because the prefetcher is a
  single sequential worker — at boot nothing is genuinely in-flight, so any
  `downloading` row is an orphan from a previous process.

The UI half of #58 (phase-aware button via a single `_applyStatus`
dispatcher, badge settling at the caching→annotating handoff in
`_onCachingDone` rather than at run end) rides on top of these: it can only
narrate the sequence faithfully once every phase event reliably arrives.

Guards: `test_routes_events.py` (replay emits current status, skips
`pending`, carries error/annotation_id), `test_prefetch_queue_repo.py` +
`test_media_prefetcher.py` (requeue + start-recovery), and the
`test_clip_annotate_feedback.py` wiring guards for the phase narration.

## Consequences

+ Caching feedback is reliable regardless of when the `EventSource`
  connects — including reconnects and fast uploads — because the badge/phase
  derive from persisted item status, not from catching a transient frame.
+ A killed/crashed dev server no longer leaves a permanently stuck cache
  spinner; the next boot self-heals the queue. (`SIGTERM` is still the
  contract for releasing the CatDV seat; recovery is the backstop for when
  it doesn't happen.)
+ Both fixes are deployment-agnostic and safe under the cloud topology:
  `--max-instances=1` means one process, so the in-process `EventBus` +
  replay never split across instances.
- Replay can re-deliver a phase the client already saw (e.g. a duplicate
  `uploading`); handlers are guarded to fire once (`_didUpload`,
  `_announcedAnnotating`, `_cachedAnnounced`), so this is harmless but is a
  property the frontend must preserve.
- The orphan-recovery assumption ("single worker ⇒ downloading-at-boot =
  orphan") is load-bearing. If the prefetcher ever gains real parallelism,
  recovery must move to a per-row lease; the constraint is documented at
  `requeue_orphans` and in `media_prefetcher.py`'s module docstring.
