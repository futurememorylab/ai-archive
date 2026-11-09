# Studio uploads straight to the bucket — design

**Date:** 2026-11-04
**Status:** Implemented
**Why now:** a researcher workshop on Prompt Studio (2026-11-05). On
production a 57.6 MB mp4 failed with 413.

## Problem

Cloud Run refuses any HTTP/1 request over 32 MiB before it reaches the app,
so the app's own limit (`studio_upload_max_mb`, 500) never applies there.
`POST /api/studio/uploads` also reads the whole file into memory and writes
it to `/data`, which on Cloud Run is in-memory too, so a large upload would
take its size twice from the instance's 2 GiB even under the limit.

## Decision

In cloud mode (`MEDIA_CACHE=ai_store` with the GCS AI store) the browser
sends the file straight to the media bucket on a V4 signed URL:

1. `POST /api/studio/uploads/direct` with `{filename, mime, size_bytes,
   duration_secs, width, height}`. The app checks type and size (415 / 413),
   reserves an `uploaded_clip` row and answers `{direct: true, clip_id,
   upload_url, mime}`. The URL allows one `PUT` with that Content-Type into
   the clip's blob (`instances/<instance>/uploads/<clip_id>.mov`, the same
   key a server upload uses) and must be started within 15 minutes.
2. The browser `PUT`s the file there (XHR, so the dropzone shows a
   percentage).
3. `POST /api/studio/uploads/direct/{clip_id}/complete` (multipart: the
   poster and `set_id`). The app reads the blob's metadata, refuses it (409)
   when it is missing or its size differs from the announced one, records it
   in `ai_store_files` like `GcsInputStore.ensure_uploaded` does, stores the
   poster and adds the clip to the set. Playback (`AiStoreBackend.locate`)
   and annotation (`clip_media.ai_input_for_clip`, status first) then find it
   with no local file.
4. When the `PUT` fails the browser calls
   `DELETE /api/studio/uploads/direct/{clip_id}`, which deletes any partial
   blob and the reserved row. Complete and abort only act on a reserved clip
   that is in no set yet (404 otherwise), so they can't touch a finished one.

Local runs (`MEDIA_CACHE=local`) have no `DirectUpload`: the start call
answers `{direct: false}` and the browser posts the file to
`/api/studio/uploads` as before. There is no request limit locally.

The bucket's CORS policy (`deploy/gcs-cors.json`) now allows `PUT` from the
app's URLs as well as `GET`/`HEAD`. It has to be applied once from a machine
with gcloud access; `deploy/check-media-cors.sh` now also fails when `PUT`
is missing.

Code: `services/direct_upload.py`, `GcsService.signed_upload_url` /
`blob_meta`, the three routes in `routes/studio.py`, `static/studioUpload.js`.
Walkthrough: `studio-upload-direct`.

## Not done

- A reserved row whose browser closed mid-upload stays in `uploaded_clip`
  (it is in no set, so nothing lists it); a blob that arrived without a
  complete call stays in the bucket. Both are small and rare; a later orphan
  sweep can take them.
- Only mp4 and webm are accepted, as before. A .mov from a camera has to be
  exported to mp4 first.

## Manual acceptance flows

Setup: the change deployed to staging, and the bucket CORS updated
(`gcloud storage buckets update gs://catdav-proxies --cors-file=deploy/gcs-cors.json`).

1. **Large upload on staging.** Open staging → Prompt Studio → Uploaded,
   open or create a set, drop a 50–100 MB mp4. The dropzone shows
   "Uploading 0/1… N%" counting up; a toast says "Uploaded <name>." and the
   card appears in the set with its poster.
2. **Play it.** Click the card: the video plays and seeks.
3. **Annotate it.** With a prompt version active, run it on the clip. The
   run finishes and its scenes show; nothing is written to the archive.
4. **Small upload still works.** Drop a <10 MB mp4: same result.
5. **Wrong type.** Drop a .mov: a toast says it was skipped (mp4/webm only)
   and no card appears.
6. **Local run.** On `./run.sh` (MEDIA_CACHE=local) drop an mp4: it uploads
   through the app as before (the network tab shows `/api/studio/uploads`,
   no storage PUT).
7. **CORS check.** `./deploy/check-media-cors.sh catdv-annotator-staging
   catdv-annotator` prints "CORS on gs://catdav-proxies covers uploads and:
   …".
