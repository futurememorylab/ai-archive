# Prompt Studio — Uploaded Clips (Spec B)

**Date:** 2026-06-08
**Status:** Approved — ready for implementation planning
**Scope:** The real upload subsystem behind the Uploaded tab Spec A stubbed.
Local-only deployment. Full vertical slice: upload a web-safe video → it is
stored, listed, playable, thumbnailed, and runnable through Gemini.

## Context

Spec A (merged, #35) renamed studio "folders" to **sets**, added a
`source` discriminator (`'archive' | 'uploaded'`), and shipped source tabs.
The **Uploaded** tab is a placeholder (`_studio_uploaded_stub.html`,
"Uploads coming soon"). This spec (**B**) makes it real.

The central tension: the entire clip pipeline is keyed on a **source-blind
integer `clip_id`** that today means a CatDV archive clip:

- `studio_set_clip.clip_id INTEGER` — set membership.
- `RunCreate.clip_id: int` — the run endpoint.
- `proxy_resolver.path_for_clip_id(clip_id)` — playback bytes.
- `thumbnail_service.get_or_fetch(clip_id)` — thumbnails.
- `GET /media/{clip_id}` and `/media/{clip_id}/thumb` — serving routes.

Uploaded videos have no CatDV id. They must acquire an identity that flows
through this pipeline with minimal disturbance to the integer-keyed code
Spec A just shipped.

Two findings make this tractable:

1. **`ai_store` is keyed on `ClipKey = (ProviderId, ProviderClipId)`**
   string tuples (`backend/app/archive/model.py:14`), e.g.
   `("catdv", "123")`. Uploaded clips use `("uploaded", str(clip_id))`
   with **no change to the AI-store layer**.
2. **`proxy_resolver.path_for_clip_id`** returns early from the
   `proxy_cache` table when a file is present and only falls back to a
   CatDV download on a miss. **Pre-seeding** a `proxy_cache` row at ingest
   makes playback "just work" with no CatDV call.

### Decisions taken during brainstorming

| Decision | Choice |
|---|---|
| Identity model | **Approach A** — synthetic id + `uploaded_clip` table + thin source guards |
| Deployment target | **Local-only** for now; do not preclude cloud later |
| Format handling | **Constrain to web-safe** (mp4/H.264, webm); reject others. **No ffmpeg/transcode.** |
| Thumbnails | **Client poster frame** captured at upload; **placeholder fallback** |
| Set rename UI | **Rename only** (no delete-set UI) on the shared set card |
| Default set | Auto-create an **"Uploads"** set when none exists |
| GCS upload timing | **Lazy** — on first run, status-then-upload (matches archive path) |
| Id scheme | **High positive offset** `clip_id = 1_000_000_000 + pk` (not negative) |

### Why high-offset ids and not negative

The serving routes use FastAPI's `int` path converter, whose regex is
`[0-9]+` — it does **not** match negative numbers, so `/media/-5` would
404 silently. A high positive offset keeps every existing route working
unchanged. CatDV clip ids on this single-operator instance will not
approach 1e9, so the ranges stay disjoint and `is_uploaded(clip_id)` is the
O(1) predicate `clip_id >= 1_000_000_000`.

### Key prior-art / reuse (do not re-implement)

| Concern | Existing thing to reuse | Location |
|---|---|---|
| Run a prompt on one clip | `POST /api/studio/runs` + `run_job` | `routes/studio.py`, `services/annotator.py` |
| Single + bulk run, selection | `studioStore` `_runOne` / `runOnSelectedClips` | `static/studioStore.js` |
| Set navigator (cards, tabs, expand) | `studioSets` component + `_studio_set*.html` | `static/studio.js`, `templates/pages/` |
| Set membership | `studio_set_clip`, `StudioSetsRepo.add_clips/remove_clip/list_clips` | `repositories/studio_sets.py` |
| Set rename (backend) | `rename_set` + `PATCH /api/studio/sets/{id}` | `repositories/studio_sets.py`, `routes/studio.py:73` |
| Proxy serving | `ProxyResolver.path_for_clip_id` + `GET /media/{clip_id}` | `services/proxy_resolver.py`, `routes/media.py` |
| Thumbnail serving | `ThumbnailService.path_for/get_or_fetch` + `GET /media/{clip_id}/thumb` | `services/thumbnail_service.py`, `routes/media.py` |
| AI-store upload (Gemini) | `AIInputStore.status/ensure_uploaded/reference_for_gemini` keyed on `ClipKey` | `archive/ai_store.py` |
| Proxy cache index | `ProxyCacheRepo.upsert/get` | `repositories/proxy_cache.py` |
| Toasts / HTMX re-init / format | `Alpine.store('toast')`, `window.htmxAlpine.reinit`, `fmtTimecode` | `static/toast.js`, `static/htmxAlpine.js`, `static/format.js` |
| User-facing error strings | `services/errors.py::humanise` | `services/errors.py` |
| Async fs I/O | `asyncio.to_thread` (see `cache_actions.py`) | — |

## Alternatives considered

- **Identity — synthetic id (A) vs `ClipRef(source,id)` refactor (B) vs
  separate upload pipeline (C).** Chosen **A**. B threads a typed reference
  through resolver / thumbnail / runs / annotator — clean but a large
  refactor across freshly-shipped Spec A code, high risk, against the
  minimal-change discipline. C gives uploads their own run/metadata/player
  path — rejected by CLAUDE.md's "don't parallel-evolve a second engine"
  (it duplicates the annotator). A confines source-awareness to ~3 guards.
- **Id range — high positive offset vs negative.** Chosen **offset**;
  negative ids 404 the `[0-9]+` int path converter (see above).
- **Format — transcode to mp4 proxy vs use-original vs constrain-web-safe.**
  Chosen **constrain**. Transcode adds an ffmpeg dependency + background
  pipeline; use-original risks unplayable codecs and bloated Gemini
  uploads. Constraining guarantees playback with zero new media tooling;
  the user pre-transcodes externally if needed.
- **Thumbnails — client poster frame vs server ffmpeg vs placeholder.**
  Chosen **client poster frame with placeholder fallback**. Because the
  format is web-safe, the browser can decode and capture a frame; no
  server-side media tooling. Fallback covers decode quirks.
- **GCS upload — eager at ingest vs lazy on first run.** Chosen **lazy**.
  Keeps ingest fast and fully offline-capable; cost only when a run needs
  the bytes, exactly as the archive path already behaves.
- **Set delete UI.** Out of scope. Rename UI is added (backend already
  exists); delete-set UI is deferred.

## Decision

### 1. Data model — `uploaded_clip` table

**Migration `0018_uploaded_clips.sql`** (next free number after `0017`):

```sql
CREATE TABLE uploaded_clip (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  original_filename TEXT    NOT NULL,
  stored_filename   TEXT    NOT NULL,
  mime              TEXT    NOT NULL,
  size_bytes        INTEGER NOT NULL,
  duration_secs     REAL,
  width             INTEGER,
  height            INTEGER,
  created_at        TEXT    NOT NULL
);
```

- **Exposed `clip_id` = `1_000_000_000 + uploaded_clip.id`.** `AUTOINCREMENT`
  guarantees a monotonic, never-reused PK so the synthetic id is stable
  even after deletes.
- A new module (e.g. `backend/app/services/uploaded_ids.py`) owns the
  constant and helpers: `UPLOAD_ID_BASE = 1_000_000_000`,
  `is_uploaded(clip_id) -> bool`, `to_clip_id(pk) -> int`,
  `to_pk(clip_id) -> int`.
- **Membership reuses `studio_set_clip` unchanged.** Uploaded clips join
  uploaded-source sets exactly like archive clips. `uploaded_clip` carries
  no `set_id` (a clip may live in multiple sets; membership lives only in
  `studio_set_clip`, same as archive).
- **New repo `UploadedClipsRepo`** (`repositories/uploaded_clips.py`):
  `create(...) -> pk`, `get(clip_id) -> row | None`,
  `get_many(clip_ids) -> {clip_id: row}` (batched via
  `_batch.chunked_in_clause`), `delete(clip_id)`.

### 2. Ingest pipeline

**`POST /api/studio/uploads`** (multipart) in `routes/studio.py`, taking:
the video file, a poster JPEG (optional), `set_id` (optional — see §4
default set), and client-derived `duration_secs` / `width` / `height`.

Steps (all blocking fs work wrapped in `asyncio.to_thread`):

1. **Validate** the content-type + extension against a web-safe allowlist
   (`video/mp4`, `video/webm`). Reject anything else with **HTTP 415** and a
   `humanise`-quality detail; the frontend toasts it. Enforce a max upload
   size (config constant, e.g. `studio_upload_max_mb`).
2. **Insert** `uploaded_clip` → `pk` → `clip_id = UPLOAD_ID_BASE + pk`.
3. **Write bytes** to `data_dir/cache/uploads/{clip_id}.{ext}`.
4. **Pre-seed `proxy_cache`** via `ProxyCacheRepo.upsert(clip_id,
   file_path=…, size_bytes=…, provider_id="uploaded",
   provider_clip_id=str(clip_id))`.
5. **Store the poster** JPEG at `thumbnail_service.path_for(clip_id)` if one
   was sent. If absent, the `/thumb` route 404s and the card renders the
   placeholder (§4).
6. **Add** to `studio_set_clip(set_id, clip_id)` via
   `StudioSetsRepo.add_clips`.
7. **Return** the rendered `_studio_set_clip_card.html` partial for an HTMX
   swap into the set's clip list — never a reload.

GCS upload is **not** performed here; it is lazy (§5).

A new bounded constant guards the in-flight ingest path (no special
concurrency control needed — uploads are user-paced and serialized by the
browser form).

### 3. Reuse map — the source guards

Source-awareness is confined to these points; everything else is unchanged.

| Layer | Archive behaviour | Uploaded change |
|---|---|---|
| `ai_store` clip key | `("catdv", str(id))` | `("uploaded", str(clip_id))` — **no code change** |
| `proxy_resolver.path_for_clip_id` | cache hit → file; miss → CatDV download | pre-seeded cache hit → file; **on miss, `if is_uploaded(clip_id)` raise `ProxyNotFound` and skip the CatDV fallback** |
| `/media/{clip_id}` playback | StreamingResponse from resolver | unchanged (resolver serves the local upload) |
| Metadata in `annotator._process_item` | `archive.get_clip(id)` for name/duration | `if is_uploaded`: read `UploadedClipsRepo.get` instead; build the same fields |
| Metadata on studio page (`_studio_set` render) | `archive.get_clip(id)` per clip | `if is_uploaded`: batch `UploadedClipsRepo.get_many`; name = filename, no archive call |
| `thumbnail_service.get_or_fetch` | CatDV thumb download | `if is_uploaded`: return the pre-stored poster path; never call CatDV |
| Runs endpoint, navigator, bulk-run, selection | int `clip_id` | **unchanged** |

`_process_item` (`services/annotator.py:297`) is the most delicate: today it
hardcodes `clip_key = ("catdv", str(item.catdv_clip_id))` and calls
`archive.get_clip`. The guard branches both the clip key and the metadata
source on `is_uploaded(clip_id)`; the proxy-path resolution and
`ai_store.status/ensure_uploaded/reference_for_gemini` calls are reused as-is.

### 4. Frontend — the Uploaded tab

- **Replace the stub** with a real uploaded-sets navigator, reusing
  `_studio_set_list.html` / `_studio_set_card.html` /
  `_studio_set_clip_card.html` and the existing `?source=uploaded`
  partition (`GET /studio/_sets?source=uploaded`).
- **Upload affordance** in the Uploaded sub-header: a dropzone + file
  picker. Uploads target the currently-expanded uploaded set; if there is
  no uploaded set, the backend **auto-creates a default "Uploads" set**
  (idempotent get-or-create on `source='uploaded'`, name `"Uploads"`) and
  the clip lands there.
- **Upload-form JS** (extend `studio.js`):
  1. On file select, load into an offscreen `<video>`, read
     `duration`/`videoWidth`/`videoHeight` from `loadedmetadata`.
  2. Seek to ~1s (clamped to `duration`), draw the frame to a `<canvas>`,
     `toBlob('image/jpeg')` → poster. Wrap in try/catch; on any failure
     send no poster (placeholder fallback).
  3. Build `FormData` (file + poster + set_id + metadata), POST via
     `XMLHttpRequest` with `upload.onprogress` for a progress bar.
  4. On success, swap the returned card partial into the set's clip list
     and `Alpine.store('toast').push('Uploaded', {level:'success'})`.
  5. On error, `humanise`d server detail → `toast` error. Never
     `location.reload()`.
- **Uploaded clip card:** the shared `_studio_set_clip_card.html` gains a
  small source-aware branch — for uploaded clips, show the **filename** as
  the name and **suppress the `id:N` tag** (the synthetic id is not
  user-meaningful). Thumbnail `<img src="/media/{clip_id}/thumb">` with an
  `onerror` placeholder. Duration overlay via `fmtTimecode`. Run-dots,
  selection checkbox, focus dot, remove-X all unchanged (all keyed on
  `clip_id`).
- **Remove-X** on an uploaded clip removes the `studio_set_clip` membership
  (existing path). Deleting the underlying file/`uploaded_clip` row when it
  leaves its last set is **out of scope** (tracked as a follow-up); the
  membership-removal UX matches archive exactly.

### 5. Run + playback path

- **Run:** the existing `POST /api/studio/runs` endpoint and `run_job` are
  unchanged. `_process_item`'s source guards (§3) make an uploaded clip
  resolve its proxy path locally, key its `ai_store` entry as
  `("uploaded", …)`, lazily `ensure_uploaded` to GCS on first run, and pull
  metadata from `UploadedClipsRepo`. Uploaded clips are runnable
  individually and via the existing bulk-run.
- **Playback:** unchanged. The player loads `/media/{clip_id}`; the
  resolver serves the pre-seeded local file.

### 6. Set rename UI (closes the Spec A gap)

Backend (`rename_set`, `PATCH /api/studio/sets/{id}`) already exists and is
source-agnostic. Add the **frontend trigger** to the shared set card so it
works for archive and uploaded sets at once:

- `_studio_set_card.html`: a rename affordance (inline edit on the set
  name, e.g. a small pencil that swaps the name span for an `<input>`).
- `studioSets` component (`static/studio.js:219`): a `renameSet(setId)`
  method that PATCHes `/api/studio/sets/{id}` with the new name, swaps the
  returned card partial (or updates the name in place), and toasts on
  error. Honors the `UNIQUE(source, name)` constraint — a duplicate name
  within the source returns an error the user sees as a toast.
- **No delete-set UI** in this spec.

### 7. Error handling & offline

- Upload validation / size / write failures → `humanise` + `toast` error,
  HTMX partial response; never `location.reload()`.
- Uploaded clips are **local + DB-backed**, so they list, thumbnail, and
  play fully **offline**. Only the *first run's* GCS upload + Gemini call
  need network; offline yields the same clear error the archive path gives.
- The `is_uploaded` guard guarantees an uploaded-clip cache miss returns
  `ProxyNotFound` (naming the upload) and **never** issues a stray CatDV
  call — preserving the cache-layer separation in CLAUDE.md.

### 8. Tests (TDD)

- **Migration:** `uploaded_clip` created with `AUTOINCREMENT`; existing
  tables/rows untouched; applying twice is a no-op (runner contract).
- **Identity:** `is_uploaded` / `to_clip_id` / `to_pk` round-trip; the
  uploaded range is disjoint from plausible CatDV ids.
- **Ingest route:** a valid mp4 upload creates the row + file +
  `proxy_cache` row + thumbnail + `studio_set_clip`, and returns the card
  partial; a non-web-safe format → 415; oversize → 413/clear error;
  missing set_id → lands in the auto-created "Uploads" set.
- **`proxy_resolver` guard:** an uploaded-id miss raises `ProxyNotFound`
  and makes **no** CatDV call (assert via a spy/fake archive).
- **`thumbnail_service` guard:** uploaded id returns the pre-stored poster;
  no CatDV download attempted.
- **`annotator._process_item`:** an uploaded clip resolves metadata from
  `UploadedClipsRepo`, keys `ai_store` with `("uploaded", …)`,
  `ensure_uploaded`s once, and runs to terminal status.
- **Studio page render:** an uploaded set's `_studio_set` partial renders
  cards with filename + duration and no `id:N` tag; metadata fetched via
  batched `get_many` (extend the existing N+1 / `assert_query_count` guard
  so statement count is flat for 10 vs 100 vs 1000 uploaded clips).
- **Set rename UI:** `renameSet` PATCHes and swaps in place; a duplicate
  name within the source surfaces a toast; archive + uploaded both rename.
- **Regression:** archive single-run, bulk-run, archive picker, and the
  sets navigator all stay green.

## Consequences

- **One small, well-bounded synthetic-id convention** (`UPLOAD_ID_BASE`)
  carries uploaded clips through the entire integer-keyed pipeline. It must
  be honored anywhere a bare `clip_id` is interpreted as a CatDV id — the
  guard sites in §3 are the complete list; a new clip-touching feature must
  consult `is_uploaded`.
- **The cache-layer separation holds.** Uploaded clips are pre-seeded into
  `proxy_cache` and the thumb cache and lazily into `ai_store`; no new cache
  layer is introduced and the offline contract (miss → graceful return) is
  preserved.
- **No new media tooling.** Constraining to web-safe formats avoids ffmpeg
  entirely; the cost is that non-web codecs are rejected at upload, pushing
  any transcode to the user. If transcoding is ever needed it becomes its
  own spec (a new ingest stage + the proxy model already in place).
- **Cloud is not precluded.** The `uploaded_clip` + synthetic-id seam is
  storage-agnostic; a later cloud spec swaps the local `cache/uploads/`
  write for a GCS-primary store without touching identity or the navigator.
- **Two deliberate follow-ups:** (a) deleting the underlying file +
  `uploaded_clip` row when a clip leaves its last set (today only the
  membership is removed); (b) delete-set UI (rename ships here, delete does
  not).

## Manual acceptance flows

Setup: a running backend (`/studio`) with at least one prompt version. GCS
(`ai_store`) reachable for the run flows; archive connection optional.

1. **Upload and list.** Open `/studio`, switch to the **Uploaded** tab.
   With no uploaded sets, drop a web-safe `.mp4` onto the dropzone (or pick
   it). Confirm: a progress bar runs; on completion a clip card appears
   under an auto-created **"Uploads"** set, showing the **filename**, a
   **poster-frame thumbnail**, and a duration overlay (no `id:N` tag). No
   page reload occurs.

2. **Reject a bad format.** Attempt to upload a non-web-safe file (e.g. a
   `.mov` ProRes or a `.txt`). Confirm an **error toast** with an
   actionable message and that **no** card is added.

3. **Thumbnail fallback.** Upload a web-safe file whose first second fails
   to decode to a frame (or simulate poster-capture failure). Confirm the
   card shows the **placeholder** instead of a broken image, and the clip is
   still listed and playable.

4. **Play an uploaded clip.** Click an uploaded clip card. Confirm it
   becomes the focused clip (focus dot + highlight) and the **player loads
   and plays** it from `/media/{clip_id}` — with the archive disconnected,
   to prove the local-only playback path.

5. **Run a prompt on an uploaded clip.** With a prompt version active, run
   it on a focused uploaded clip. Confirm the run reaches terminal status,
   the clip gains the active-version **run-dot**, and the output is saved
   (open it to verify). Re-run and confirm the second run **reuses** the
   GCS upload (no re-upload — check telemetry/logs).

6. **Bulk run on uploads.** Select two or three uploaded clips (checkboxes
   or the set checkbox), confirm the **bulk-action bar** shows "Run on N
   clips", run it, and confirm progress (1/N → N/N) and that all clips gain
   the run-dot. Force one to fail and confirm a toast plus the others
   completing.

7. **Rename a set (both sources).** Rename the "Uploads" set inline; confirm
   the name updates in place with no reload. Switch to the **Archive** tab
   and rename an archive set the same way. Attempt to rename a set to a name
   already used **within the same source** and confirm an error toast;
   confirm the **same name across sources** is allowed.

8. **Regressions hold.** On the Archive tab, single-clip run, bulk-run, and
   the "+ Add from archive" picker all still work. With the archive offline,
   the picker shows its existing clear error; uploaded clips remain fully
   listable and playable.
