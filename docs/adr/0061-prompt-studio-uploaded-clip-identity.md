# 0061. Prompt Studio uploaded clips — synthetic high-offset id + thin source guards

**Date:** 2026-06-08
**Status:** Accepted
**Lifespan:** Feature

## Context

Spec A (#35) shipped the studio "sets" navigator with a `source`
discriminator (`'archive' | 'uploaded'`) but left the **Uploaded** tab a
stub. Spec B
(`docs/specs/2026-06-08-prompt-studio-uploads-spec-b-design.md`) makes it
real: upload a web-safe video → store it locally → list / thumbnail /
play it → run a Gemini prompt on it.

The whole clip pipeline is keyed on a **source-blind integer `clip_id`**
that today means a CatDV archive clip: `studio_set_clip.clip_id`,
`RunCreate.clip_id`, `proxy_resolver.path_for_clip_id`,
`thumbnail_service.get_or_fetch`, and the `GET /media/{clip_id}` +
`/media/{clip_id}/thumb` serving routes. Uploaded videos have no CatDV
id, yet must flow through this pipeline with minimal disturbance to the
integer-keyed code Spec A just shipped.

## Alternatives

- **Identity — synthetic id (A) vs a typed `ClipRef(source, id)` refactor
  (B) vs a separate upload pipeline (C).** B threads a typed reference
  through resolver / thumbnail / runs / annotator — clean but a large,
  high-risk refactor across freshly-shipped Spec A code, against the
  minimal-change discipline. C duplicates the annotator/run/player path —
  rejected by CLAUDE.md's "don't parallel-evolve a second engine". Chosen
  **A**: it confines source-awareness to a short, enumerable list of
  guards.
- **Id range — high positive offset vs negative.** Negative ids 404 the
  FastAPI `int` path converter (`[0-9]+`), silently breaking
  `/media/-5`. Chosen **high positive offset**.
- **Format — transcode vs use-original vs constrain-web-safe.** Chosen
  **constrain** (mp4/H.264, webm) — zero new media tooling, guaranteed
  playback; the user pre-transcodes externally if needed.
- **Thumbnails — server ffmpeg vs client poster frame vs placeholder.**
  Chosen **client poster frame at upload, with a placeholder fallback** —
  the web-safe constraint lets the browser decode a frame; no server media
  tooling.
- **GCS upload — eager at ingest vs lazy on first run.** Chosen **lazy**,
  matching the archive path; keeps ingest fast and fully offline-capable.

## Decision

- **`clip_id = UPLOAD_ID_BASE + uploaded_clip.id`** with
  `UPLOAD_ID_BASE = 1_000_000_000` (`backend/app/uploaded_ids.py`:
  `is_uploaded` / `to_clip_id` / `to_pk`). A leaf module importing nothing,
  so repositories may use it without breaking the "repos don't import
  services" contract. `AUTOINCREMENT` on `uploaded_clip` guarantees ids are
  never reused, so a deleted upload's synthetic id can't later collide with
  a different file. On a single-operator CatDV instance the two ranges stay
  disjoint, making `is_uploaded(clip_id)` an O(1) predicate.
- **Per-upload metadata** lives in a new `uploaded_clip` table (migration
  `0018`) + `UploadedClipsRepo`. Set membership reuses `studio_set_clip`
  unchanged.
- **Ingest** (`POST /api/studio/uploads`) writes bytes to
  `data_dir/cache/uploads/`, pre-seeds a `proxy_cache` row, stores the
  client poster into the thumb cache, and adds membership — so the existing
  serving routes, `ai_store` (keyed `("uploaded", str(clip_id))`), and the
  run engine work unchanged. All blocking fs work runs in
  `asyncio.to_thread`.
- **The complete guard list** (everything else is source-blind):
  `proxy_resolver` (a thin `UploadAwareResolver` wrapper — uploaded miss →
  `ProxyNotFound`, never CatDV), `thumbnail_service.get_or_fetch`
  (uploaded miss → `None`, never CatDV), `annotator._resolve_clip_meta`
  (uploaded → `UploadedClipsRepo`, else `archive.get_clip`), the
  `_studio_set` page render (batched `get_many`, filename as name), and the
  clip card (filename, suppressed `id:` tag, `<img>` poster + placeholder).

### Two implementation-time calls beyond the plan's literal text

1. **Uploaded media/thumb serve from `CoreCtx`, not `LiveCtx`.** The
   `/media/{clip_id}` and `/media/{clip_id}/thumb` routes went through
   `get_live_ctx`, which returns a typed 503 when offline — but spec §7 and
   acceptance flow 4 require uploaded clips to list, thumbnail, and **play
   fully offline** from the local DB-backed cache. Both routes now branch on
   `is_uploaded(clip_id)` first and resolve DB-first via the core context
   (`proxy_cache_repo` for video, the `data_dir/cache/thumbs` poster for
   the thumbnail), bypassing the live gate. The archive path is unchanged.
2. **Lazy archive resolution in `annotator._process_item`.** The plan's
   verbatim refactor hoisted the full metadata resolve (which calls
   `archive.get_clip` for archive clips) to the **top** of `_process_item`,
   ahead of the proxy/AI-store cache-miss short-circuit — making a
   not-cached clip call CatDV on a path that is going to fail with
   `ProxyNotFound`. That violates the cache/seat-conservation discipline in
   CLAUDE.md (and the existing
   `test_run_fails_clearly_when_neither_cached` invariant), and the spec
   says the proxy/ai-store flow is "reused as-is". We instead compute the
   cheap `clip_key` (no archive call) up front and call `_resolve_clip_meta`
   **after** the cache-miss short-circuit, so the single `archive.get_clip`
   happens only once the clip is known to be available.

## Consequences

- One small, well-bounded synthetic-id convention carries uploaded clips
  through the entire integer-keyed pipeline. It must be honored anywhere a
  bare `clip_id` is interpreted as a CatDV id — the guard sites above are
  the complete list; any new clip-touching feature must consult
  `is_uploaded`.
- The cache-layer separation holds: uploaded clips are pre-seeded into
  `proxy_cache` + the thumb cache and lazily into `ai_store`; no new cache
  layer; the offline contract (miss → graceful return) is preserved, and an
  uploaded miss never issues a stray CatDV call.
- No new media tooling — non-web codecs are rejected at upload; any
  transcode is pushed to the user (a future spec if ever needed).
- Cloud is not precluded: the `uploaded_clip` + synthetic-id seam is
  storage-agnostic; a later spec can swap the local `cache/uploads/` write
  for a GCS-primary store without touching identity or the navigator.
- **Two deliberate follow-ups (out of scope):** (a) deleting the underlying
  file + `uploaded_clip` row when a clip leaves its last set (today only
  membership is removed); (b) delete-set UI (rename ships here, delete does
  not).
