# 0026. Image (still) clip support via original-media fetch

**Date:** 2026-05-26
**Status:** Accepted

## Context

Still-image clips were inaccessible: blank viewer, failed "cache", no
annotation. Investigation showed these stills were imported with
`pragafilm.generuj.proxy = false`, so CatDV generated no proxy, poster, or
thumbnail. The app only ever used the clip-scoped proxy path
(`GET /clips/{id}/media`), which 404s for stills, and always rendered a
`<video>` element. The app runs on a separate host from CatDV, so the
originals on `/Volumes/ARECA/...` are not reachable via the filesystem.

## Alternatives

- **Full-width poster** — rejected: stills have no poster/thumbnail at all.
- **Filesystem read of the original** — rejected: the media volume is not
  (and will not be) mounted on the app host; deployment stays split-host.
- **Re-enable proxy/poster generation in CatDV** — rejected: CatDV-admin
  work, and transcoding a still into a video proxy is pointless.

## Decision

Fetch the original over REST: `GET /api/9/media/{mediaID}?type=orig`
(verified to return the original JPEG; `type=proxy` 404s for stills).
`mediaID` is `clip.provider_data.media.ID`. Classify a clip as an image by
its `media.filePath` extension (authoritative — `media.still` was observed
`false` on a real JPEG). The resolver caches the original as `{id}.{ext}`,
the thumbnail service downscales it to a cached poster with Pillow, and the
detail template renders `<img>`. The existing Gemini pipeline is unchanged:
it MIME-guesses from the cached path and already skips the timecode anchor
at `duration == 0`.

## Consequences

- New runtime dependency: Pillow (smallest self-contained option; rejected
  pyvips/Wand/ffmpeg, which need native libs/binaries).
- The GCS blob name stays `clips/{id}.mov` for images — cosmetic only, since
  Gemini consumes the passed `mime_type`, not the blob name.
- Formats Pillow can't decode natively (e.g. HEIC) degrade to the list
  placeholder; the detail `<img>` still points at the original bytes.
- Whole-image annotation only (no timecode markers); the prompt/target-map
  content for stills is configured separately by the operator.
