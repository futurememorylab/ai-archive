# 0111. Walkthrough tests run the app in-process with injected fakes

- **Date:** 2026-06-22
- **Status:** Accepted
- **Lifespan:** Invariant

## Context

We want annotated Playwright walkthrough videos — both for review and as
user-facing documentation of the key flows. But the web UI computes
`clip_id = int(clip.key[1])` (`backend/app/ui/view_models.py`), so it
requires numeric clip keys. The filesystem archive provider
(`ARCHIVE_PROVIDER=fs`) yields path-string keys (`archive_30s/clip001`)
that fail `int()`, so it cannot render the clips list or the clip-detail
page — it only backs the write-stack tests. To render those pages the
walkthrough harness must supply a numeric-keyed archive, which the page
tests already do by injecting a `FakeArchive` in-process via
`tests/_helpers/live_ctx.py::install_live_ctx`.

## Alternatives

- **Subprocess app + `ARCHIVE_PROVIDER=fs`.** Rejected — the fs provider's
  path-string keys fail `int(clip.key[1])`, so it cannot render the clips
  list / clip-detail page that the walkthrough exists to film.
- **Node `@playwright/test`.** Rejected — ADR 0001 commits to a Python-only
  stack with no Node; the Python Playwright binding exposes the same v1.59
  `page.screencast` API, so Node buys nothing here.
- **Wire the real `SyncEngine` + a writable numeric archive for a genuine
  sidecar writeback.** Deferred — the chosen publish fidelity is
  queue-level (real `clip_versions` + `pending_operations` rows), which is
  enough to film the flow; a full SyncEngine round-trip is more harness
  code and the riskiest part. Left as a follow-on.

## Decision

Run the FastAPI app in-process via `uvicorn.Server` on a daemon thread
(a real socket on `127.0.0.1:8766` so Playwright drives a true browser),
then `install_live_ctx` with a numeric-keyed `FakeArchive` + a
`LocalFileResolver` over a real ffmpeg-seeded proxy video + a
`media_cache_backend` over that resolver. The draft state (annotation +
`review_items`) is seeded into the DB on the server's own event loop.
Publish is exercised against the durable write queue — producing real
`clip_versions` and `pending_operations` rows — rather than a SyncEngine
round-trip. The harness uses Playwright's v1.59 `page.screencast` API to
overlay a chapter card, a step overlay, and action highlights. The entry
point is the `/e2e` skill.

## Consequences

Runs fully offline with no CatDV seat. One clip carries both published
state (the `FakeArchive` clip fields) and draft state (the DB rows). New
dev dependency `playwright>=1.59` plus a one-time
`playwright install chromium`, and ffmpeg is required to seed the proxy
video. A few inert `data-test=` template hooks were added (covered by the
design-language guard). The MVP ships one flow (clip-detail
review → edit → publish); follow-ons — more flows, GCS upload,
generate-on-camera, and a real SyncEngine writeback — reuse the same
harness. Cross-ref ADR 0001.
