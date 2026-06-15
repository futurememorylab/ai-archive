# 0081. Upload orphan-GC on set removal (reference-count)

- **Date:** 2026-06-15
- **Status:** Accepted

## Context

Removing an uploaded clip in the studio UI hit
`DELETE /sets/{set_id}/clips/{clip_id}` → `studio_sets_repo.remove_clip()`,
which only deleted the `studio_set_clip` membership row. The upload's own
artifacts leaked: the `uploaded_clip` row (now unreachable, in no set), the
local cached video `cache/uploads/{clip_id}.mp4`, the local poster
`cache/thumbs/{clip_id}.jpg`, and — in `MEDIA_CACHE=ai_store` mode — the GCS
blob plus its `ai_store_files` row. Each delete+reupload leaked ~the file
size on the seat-/disk-constrained local box and on ephemeral Cloud Run
(issue #57, follow-up to #55).

An uploaded clip *may* belong to more than one set, so removing it from one
set must not delete bytes still referenced elsewhere.

## Alternatives

- **A distinct explicit "Delete upload" action** separate from "Remove from
  set". Rejected for now: it adds UI surface and still leaves the
  remove-from-last-set path leaking unless it *also* GCs. The acceptance
  flow in #57 is phrased as "remove from its only set → everything gone",
  i.e. reference-count semantics.
- **Cascade-delete the upload whenever any membership is removed.** Wrong:
  it would destroy a clip still in another set.
- **A new cache layer / bespoke deletes in the route.** Rejected — CLAUDE.md
  mandates routing byte removal through the existing cache services, and the
  route should stay thin.

## Decision

Reference-count orphan-GC on removal. After `remove_clip`, if the clip is
uploaded (`is_uploaded`) **and** now referenced by zero sets
(`StudioSetsRepo.count_sets_for_clip == 0`), a new `UploadCleanup` service
garbage-collects the upload by reusing the existing layers:

- proxy bytes + ai-store blob + their index rows →
  `CacheActions.evict_clip_everywhere(("uploaded", str(clip_id)), force=True)`,
- poster (local JPEG + durable GCS thumb) → `ThumbnailService.evict()`
  (new), backed by `ThumbnailStore.delete()` / `GcsService.delete_thumb()`
  (new),
- `uploaded_clip` row → `UploadedClipsRepo.delete()`.

Archive clips are canonical and shared, so they are never GC'd on set
removal — only uploaded ids are.

## Consequences

- No more orphaned `uploaded_clip` rows or leaked video/poster bytes; a clip
  still in another set is untouched.
- Offline-safe by construction: `evict_clip_everywhere` unlinks the local
  file and prunes the local index even when GCS is unreachable, and a failed
  `ai_store.evict()` returns an error outcome that *leaves* the
  `ai_store_files` row in place — so the bucket-side delete stays retryable
  via orphan GC — without blocking the local + DB cleanup. `ThumbnailService.evict`
  is best-effort (missing local file and durable-store failures are swallowed).
- `UploadCleanup` is constructed per-request in the route from the CoreCtx
  (cache services, repos) plus the LiveCtx's `thumbnail_service` when wired,
  keeping the context dataclasses unchanged (no new god-context fields).
- GC routes through `CacheActions`, so each eviction is audit-logged in
  `cache_actions_log` under `who="upload-gc"`.
