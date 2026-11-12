# ADR 0135 — Studio uploads go straight to the bucket on a signed PUT URL

Date: 2026-11-12 · Status: accepted · Lifespan: Invariant (3, 4, 6) ·
Recorded after the fact for PR #165 (merged 2026-11-04) · Refines 0061,
0081, 0082 · Spec: `docs/specs/2026-11-04-studio-direct-upload-design.md`

## Context

Cloud Run refuses any HTTP/1 request over 32 MiB before it reaches the app,
so a 57.6 MB Studio upload failed with 413 on production, and `/data` on
Cloud Run is in memory, so even an allowed upload cost its size twice. PR
#165 moved cloud uploads off the app. In doing so it added a second writer
of `ai_store_files` beside the `AIInputStore` port and a manual bucket CORS
step, which touch invariants 3, 4 and 6. No ADR recorded it; this does
(SDLC audit, 2026-11-12).

## Decision

- **Cloud only.** When `MEDIA_CACHE=ai_store` with the GCS store,
  `services/direct_upload.py::DirectUpload` exists; otherwise
  `build_direct_upload` returns `None` and the browser posts through the app
  as before.
- **Signed V4 PUT URL.** The app checks type and size, reserves an
  `uploaded_clip` row and returns a URL that allows one `PUT` with that
  Content-Type into the clip's blob, started within 15 minutes. The browser
  sends the bytes straight to GCS.
- **The app verifies, then registers.** On `complete` the app reads the
  blob's metadata and refuses (409) a missing blob, a size different from
  the one announced (and checked against the cap) or another Content-Type.
  Then it upserts the `ai_store_files` row itself, the same row
  `GcsInputStore.ensure_uploaded` writes, so `AiStoreBackend.locate` and
  `clip_media.ai_input_for_clip` find it with no local file.
- **Bucket CORS is a manual, once-per-bucket step.** `deploy/gcs-cors.json`
  now allows `PUT` from the app's origins;
  `deploy/check-media-cors.sh` fails when `PUT` is missing.

## Why this is allowed beside the port (invariants 3, 4, 6)

The `AIInputStore` port uploads from a local file; a direct upload never
has one. `DirectUpload` is the one other code path that may write
`ai_store_files`, and only for `provider_id='uploaded'` clips after
verifying the blob. Everything that **reads** media still goes through the
port and the cache layers. Invariant 6 holds because the blob is checked
for size and type before it is registered, and orphan-GC on set removal
(0081) covers it like any upload.

## Known issue: the dedup column holds an MD5, not a SHA-256

`ai_store_files.sha256` is the GCS store's dedup key
(`dedup_by_sha256=True`: `ensure_uploaded` skips a re-upload when the
local file's SHA-256 equals the row's). A direct upload has no local file
to hash, so it stores `sha256="md5:<gcs md5>"` (`direct_upload.py`, the
`upsert` in `complete`), or `"md5:"` if GCS gave no MD5.

Today nothing compares it: an uploaded clip never goes through
`ensure_uploaded` with a local file. If that ever changes, the comparison
never matches and the clip is uploaded again (wasted transfer, not wrong
bytes). Fix options when it matters: a separate `md5` column, or hashing
the blob server-side on `complete`. Not opened as an issue yet; that is
Peter's call.

## Consequences

- A new bucket, project or app origin needs the CORS step, or uploads fail
  in the browser with a CORS error.
- A reserved row whose browser closed mid-upload stays in `uploaded_clip`,
  and a blob sent without `complete` stays in the bucket. Small and rare; a
  later orphan sweep can take them.
- Signing reuses the runtime service account's existing IAM `signBlob`
  grant (`roles/iam.serviceAccountTokenCreator` on itself,
  `deploy/README.md`), already needed for playback URLs.

## Alternatives considered

1. **Chunked upload through the app.** Stays under 32 MiB per request, but
   the bytes still cross the instance and its in-memory disk. Rejected.
2. **GCS resumable upload session.** Better for very large files, more
   browser code. Possible later; single PUT covers today's 500 MB cap.
