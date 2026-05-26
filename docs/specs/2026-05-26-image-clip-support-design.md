# Image (still) clip support

**Date:** 2026-05-26
**Status:** Approved (design)

## Problem

Image clips in the archive (e.g. `Abramcukova Anna 101.JPG`) are
effectively inaccessible: list previews show only the empty placeholder,
the detail view is blank, the "Cache video" action 404s, and they can't
be annotated. The app was built end-to-end for time-based video proxies,
and stills hit every video-only assumption.

### Root cause (verified)

These stills were imported with proxy generation disabled — the clip
metadata carries `fields["pragafilm.generuj.proxy"] = "false"`. As a
result CatDV never produced a proxy, poster, or thumbnail for them:

- `posterID` is `null`, `thumbnailIDs` is `[]`.
- `GET /catdv/api/9/clips/{id}/media` (the only media path the app uses)
  returns **404** — there is no proxy to serve.
- `GET /catdv/api/9/thumbnail/{id}` returns 404 ("no thumbnail").

The app's `RestProxyResolver` always downloads via the clip-scoped proxy
path and writes the result to a hardcoded `{clip_id}.mov`
(`proxy_resolver.py:50`); the detail template always renders a `<video>`
element (`clip_detail.html:94`); the media route falls back to
`video/quicktime`. None of this works for a still, and there is no
image code path anywhere in the backend.

The pixels do exist: the original full-resolution file is on the CatDV
server (`media.filePath`, e.g. `/Volumes/ARECA/.../*.JPG`), and CatDV
**will serve the original over REST**.

### The endpoint (verified live)

`GET /catdv/api/9/media/{mediaID}?type=orig` returns the original file.
Confirmed against the running server:

```
GET /catdv/api/9/media/881519?type=orig  → 200, image/jpeg, 830711 bytes (2048x1536 JPEG)
GET /catdv/api/9/media/881519?type=proxy → 404  (no proxy, default behaviour)
```

`mediaID` is `clip.provider_data.media.ID`, already present in our cached
clip metadata. (`type` defaults to `proxy`; `type=orig` is what unlocks
stills.)

## Goals

- Image clips are viewable in the detail view (full-resolution original).
- Image clips can be cached locally (same proxy-cache machinery).
- Image clips can be annotated through the existing Gemini pipeline,
  producing the same annotation/review output, stored in the same place.
- Image clips show a real thumbnail in the list/grid.

## Non-goals (out of scope)

- Region / bounding-box annotation on images (timecode-free whole-image
  annotation only).
- Re-enabling proxy/poster generation in CatDV, or any CatDV-side
  reprocessing.
- Filesystem/host-local access to originals — the app runs on a separate
  host from CatDV and will continue to; everything goes through REST.
- HEIC/RAW or other formats Pillow can't decode natively (graceful
  degradation only — see below).
- Video originals (`type=orig` for video) — videos keep using the proxy.

## Decisions (from brainstorming)

- **Image source:** original via `GET /media/{mediaID}?type=orig`.
  Rejected: full-width poster (no poster exists), filesystem read
  (volume not mounted on the app host), CatDV reprocessing (admin work,
  and transcoding a still to a video proxy is pointless).
- **Image detection:** by extension of `media.filePath` against a known
  image set (`.jpg .jpeg .png .tif .tiff .gif .bmp .webp .heic`).
  Rejected `duration == 0` and `media.still` as primary signals —
  `media.still` was observed `false` on a real JPEG, so it is unreliable;
  extension is authoritative.
- **List thumbnail:** downscale the original once to a cached poster
  (`{clip_id}.jpg`) via Pillow. Rejected serving the full original per
  row (heavy lists) and leaving the placeholder (worse UX).
- **Detail viewer:** render `<img>` for images, `<video>` for video.
  The duration-gated timeline/transport already hides itself at
  `duration == 0`, so no extra gating is needed.

## Design

### 1. Clip kind

Add a derived `kind` ∈ {`"image"`, `"video"`} to the clip view model,
computed from the `media.filePath` extension (image set above → `image`,
else `video`). A single helper (e.g. `is_image_path(path: str) -> bool`)
is the one source of truth, reused by the resolver, thumbnail service,
and view model.

### 2. Fetch the original

Add `CatdvClient.download_original(media_id: int, dest: Path)` mirroring
`download_proxy`: `GET /catdv/api/9/media/{media_id}?type=orig`, same
auth / relogin / stream-to-file handling. The existing `_is_auth_envelope`
guard applies (a JSON envelope instead of image bytes ⇒ relogin).

### 3. Resolver branch (RestProxyResolver)

`RestProxyResolver` gains an `archive` reference so it can read the
clip's `media.ID` and extension.

`path_for_clip_id(clip_id)`:
- Look up the clip; determine `kind` from `media.filePath`.
- **Image:** `dest = cache_dir / f"{clip_id}{ext}"` (real extension, e.g.
  `.jpg`); download via `download_original(media_id, dest)`.
- **Video:** unchanged — `{clip_id}.mov` via `download_proxy`.
- Record into `proxy_cache` as today (the row stores the full path, so a
  variable extension is fine).

Audit `{clip_id}.mov` assumptions elsewhere (`cache_inspector`,
`lru_eviction`, `proxy_cache` reconciler) — they should key off the
recorded `file_path`, not reconstruct `.mov`. Any that reconstruct must
be updated to use the stored path.

`LocalCacheOnlyResolver` already returns the recorded `file_path`
verbatim, so offline mode needs no change once the row exists.

### 4. Serve (media route)

No change required: `/api/media/{id}` already does
`mimetypes.guess_type(path)`, so a `.jpg` file is served as
`image/jpeg`. Range handling is harmless for images.

### 5. Detail viewer

In `clip_detail.html`, render `<img src="{{ clip.media_url }}">` when
`clip.kind == "image"`, else the existing `<video>`. The transport,
Live button, and marker timeline are already gated on
`clip.duration_secs` and stay hidden for stills.

### 6. List thumbnail (ThumbnailService)

`get_or_fetch(clip_id)` currently fetches the clip, reads
`posterID`/`thumbnailIDs`, and downloads a poster. Extend: when there is
no poster/thumbnail **and** the clip is an image, fetch the original via
`download_original(media_id, tmp)`, downscale with Pillow to a bounded
size (e.g. max 480px long edge), and save the cached `{clip_id}.jpg`.

- Run Pillow off the event loop (`asyncio.to_thread`), matching the
  existing file-write pattern.
- On decode failure (unsupported format), return `None` so the list
  shows its existing placeholder rather than erroring.
- Add `Pillow` to project dependencies.

### 7. Annotation pipeline — no structural change

Once the resolver returns `{clip_id}.jpg`:
- `annotator.py:139` guesses `image/jpeg` from the path.
- `ai_store.ensure_uploaded(..., mime)` uploads with that MIME; Gemini
  reads images natively from the `gs://` reference.
- `_render_prompt` already returns the body unchanged when
  `duration_secs <= 0` (`annotator.py:31`), so no fabricated timecode
  anchor.

The GCS blob is named `clips/{id}.mov` regardless (`gcs.py:23`) — this is
cosmetic because Gemini uses the passed `mime_type`, not the blob name.
Optional polish: include the real extension in the blob name. Not
required for correctness.

The prompt body and `target_map` for image annotation (whole-image,
non-timecode output) are a content/config change owned by the user, not
part of this code change.

## Data flow (image clip)

```
list:   /api/media/{id}/thumb → ThumbnailService → download_original(mediaID) → Pillow downscale → {id}.jpg
detail: <img src="/api/media/{id}"> → RestProxyResolver → download_original(mediaID) → {id}.jpg → served image/jpeg
cache:  POST /api/cache/prefetch → media_prefetcher → resolver.path_for_clip_id → {id}.jpg recorded in proxy_cache
annot:  run_job → resolver.path_for_clip_id → {id}.jpg → guess_type image/jpeg → GCS upload → Gemini → annotation/review
```

## Error handling

- `download_original` 404 / auth envelope: surfaced like `download_proxy`
  today (media route → 404 "proxy unavailable"; job item → error).
- Missing `media.ID` in metadata: treat as unresolvable (404 / item
  error) with a clear message.
- Pillow decode failure in thumbnail path: return `None` → placeholder.

## Testing

- Unit: `is_image_path` extension classification (positive/negative,
  case-insensitive, no-extension).
- Unit: view-model `kind` derivation.
- Integration: `CatdvClient.download_original` hits
  `/media/{id}?type=orig` and streams bytes (httpx mock / respx), incl.
  the auth-envelope relogin path.
- Integration: `RestProxyResolver.path_for_clip_id` for an image clip
  downloads via `download_original`, writes `{id}.jpg`, and records the
  correct `file_path` in `proxy_cache`; video path unchanged.
- Integration: `ThumbnailService` builds a downscaled `{id}.jpg` for an
  image with no poster, and returns `None` on a decode failure.
- Template: detail renders `<img>` for `kind=="image"`, `<video>` for
  video.
- Annotation: `annotator` resolves an image clip to a `.jpg` path and the
  computed MIME is `image/jpeg` (no timecode anchor in the rendered
  prompt).
