# 0070. GCS proxy upload is content-aware, not presence-only

**Date:** 2026-06-10
**Status:** Accepted

## Context

`GcsService.upload_if_absent` named proxy blobs `clips/<clip_id>.mov` (keyed
only on the numeric id) and skipped the upload whenever `blob.exists()` was
true. GCS objects, however, outlive their DB parent: a blob can persist after
its `uploaded_clips` / `ai_store_files` rows are gone (orphan), and the
`uploaded_clips` autoincrement id can be reissued on a fresh/rebuilt DB. When a
reissued clip_id collided with a stale orphan blob, the new upload was silently
dropped and playback (a 307 signed URL) served the **old** bytes.

Observed live on Cloud Run rev `00004-87v` while testing ADR 0069: GCS held an
orphan `clips/1000000004.mov` (no DB parent), and a fresh studio upload assigned
clip_id `1000000001` returned 201 yet the GCS object kept its two-days-old
timestamp — the bytes were never written. It was harmless only because that
upload's content happened to be byte-identical to the orphan.

## Alternatives

- **Presence-only (status quo):** cheapest, but blob presence is not proof of
  content identity — the silent-stale-bytes failure above.
- **Always overwrite on the write path:** correct, but re-uploads multi-hundred-MB
  proxies even when the stored bytes already match (cost/latency regression on
  the common re-prefetch case).
- **Content-aware skip (chosen):** compare the stored `blob.md5_hash` (a metadata
  read, no download) against the local file's MD5; upload only when absent or
  mismatched. Correct, and still skips the redundant upload when content matches.

## Decision

`upload_if_absent` reads the existing blob via `bucket.get_blob(name)` and
re-uploads (overwriting) unless the stored `md5_hash` equals the local file's
base64 MD5. The local hash is computed only on the blob-exists path, so the
common blob-absent path is unchanged. MD5 (not the adapter's sha256) is used
because that is the digest GCS exposes as object metadata.

This does not retroactively heal blobs already corrupted by the old behavior,
nor does it address orphan-blob accumulation — GCS proxy lifecycle/eviction
remains the out-of-scope item flagged in ADR 0069.

## Consequences

+ A reused clip_id with different content overwrites the stale blob; playback
  can no longer serve another clip's bytes.
+ Byte-identical re-uploads still skip the transfer (one metadata read + one
  local MD5 pass, no download).
- One extra full local read to hash on the blob-exists path; negligible next to
  the upload it guards, and skipped entirely when the blob is absent.
- Relies on GCS populating `md5_hash` (true for single-shot and resumable
  uploads of non-composite objects, which is how proxies are written).
