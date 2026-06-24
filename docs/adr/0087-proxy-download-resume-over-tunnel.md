# 0087. Resumable proxy download over the low-MTU cloud tunnel

**Date:** 2026-06-15
**Status:** Accepted
**Lifespan:** Invariant

## Context

In cloud (`media_cache=ai_store`) "Cache video" — and annotating an
un-seeded clip — pulls the proxy from CatDV through the WireGuard/onetun
tunnel and uploads it to GCS (`AiStoreBackend.ensure_cached` →
`proxy_resolver.path_for_clip_id` → `CatdvClient.download_proxy` →
`ai_store.ensure_uploaded`).

The tunnel is capped at `ONETUN_MTU=1000` because it was provisioned for
*small CatDV REST traffic only* — the original design assumed proxy media
would already be in GCS (see `deploy/cloudrun.env.yaml`). A single sustained
media stream over that tiny MTU regularly **stalls or is cut mid-body**.
`download_proxy` made one streaming attempt, which produced two failures
observed live on clip 888894:

1. **Hang then error.** A stall raised `httpx.ReadTimeout` after the 60 s
   client timeout; the prefetch job failed and the cache spinner had spun the
   whole time. No media cached.
2. **Silent truncation → corrupt GCS blob.** When the peer closed the
   connection cleanly mid-stream (no exception), `aiter_bytes` simply ended,
   so `download_proxy` *returned as if complete* with a partial file. That
   5,242,880-byte (exactly 5.00 MiB) truncation was uploaded to GCS,
   overwriting the full 11,857,062-byte proxy — there is no object
   versioning, so the good copy was lost.

`download_proxy` already sent an HTTP `Range` header to resume from an
on-disk partial, but nothing ever retried, and nothing verified the received
size against the server's declared total.

## Alternatives

- **Seed GCS from the on-prem/Mac app only; never pull media through the
  tunnel.** This is the original architecture, but it makes the cloud unable
  to cache any clip not already uploaded elsewhere, and the cross-deployment
  `ai_store` DB index means Mac-seeded blobs aren't visible to the cloud
  anyway (a separate gap). Rejected as the *primary* answer — we want the
  cloud to be self-sufficient.
- **Raise `ONETUN_MTU` so a full-size stream fits.** The path MTU to
  `gw.pragafilm.cz` is below ~1440 B on the wire; any value whose wire packet
  exceeds it black-holes *all* tunnel traffic (ADR 0074/0076). Raising MTU
  risks breaking even the REST calls that currently work. Wrong direction.
- **Just lengthen the read timeout.** Doesn't help a hard stall (zero bytes
  for minutes) and does nothing for the clean-early-close truncation.

## Decision

Make `download_proxy` resilient at the application layer: resume across
stalls and verify completeness, instead of trusting one stream.

- Loop: each iteration resumes from the on-disk partial via
  `Range: bytes=<existing>-`, until the file reaches the server's **declared
  total** (`Content-Range`'s `/total`, or `Content-Length`). The total is the
  authority on "done" — a stream that ends short (timeout *or* clean cut) just
  resumes.
- Per-read timeout is shortened to `PROXY_STREAM_READ_TIMEOUT_S = 30 s` (via a
  request-scoped `httpx.Timeout`, leaving the client default for other calls)
  so a stall is detected and resumed quickly rather than waiting out 60 s.
- Bail only after `PROXY_MAX_STALLED_ATTEMPTS = 4` consecutive **zero-progress**
  attempts (a genuinely dead link), raising so the prefetch job marks an
  error. A slow-but-advancing tunnel keeps going regardless of how many
  segments it takes.
- When the server got a `Range` request but answered `200` (ignored Range),
  treat it as a full body and rewrite from byte 0 rather than appending onto
  the partial.

The upload side already self-heals the corruption: `gcs.upload_if_absent` is
md5-content-aware (ADR-adjacent to the stale-bytes fix), so once the complete
proxy is assembled, the differing-hash 5 MiB blob is overwritten with correct
bytes on the next successful cache. No separate repair step is needed.

Guards: `test_download_proxy_completes_across_capped_chunks` (a fake that
serves a bounded slice per request must still reassemble the whole file) and
`test_download_proxy_raises_when_link_makes_no_progress` (zero-progress link
bails, never claims completion). The fake gained a `media_chunk_cap` knob.

## Consequences

+ The cloud can cache proxy media directly through the tunnel: a ~12 MB proxy
  that stalls every few MB completes across a handful of resumes.
+ Truncated downloads can no longer masquerade as complete, so a partial
  can't be uploaded and can't clobber a good GCS blob.
+ Failure is bounded and honest: a dead link errors after 4 idle attempts
  instead of hanging, so the cache spinner resolves to an error toast.
- Worst case is slow: a link that stalls every 30 s on a large (hundreds of
  MB) original will take many resume cycles. Acceptable for a background
  cache job; proxies are the common case and are small.
- Completeness can't be verified when the server declares neither
  `Content-Range` nor `Content-Length`; we fall back to "a cleanly-finished
  stream is complete." CatDV's media endpoint sends `Content-Length`, so this
  is a theoretical gap, logged for future hardening if a length-less server
  ever appears.
- The cross-deployment `ai_store` index gap (cloud can't see Mac-seeded GCS
  blobs without re-uploading) is **not** addressed here; it's a separate
  decision. This ADR only makes the tunnel pull itself reliable.
