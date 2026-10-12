# Uploads multi-instance storage — design spec

**Issue:** [#55 — Uploads multi instance storage](https://github.com/futurememorylab/ai-archive/issues/55)
**Date:** 2026-06-15
**Status:** Approved (Approach A)

## Problem

When the app runs in more than one place — a local dev machine and the
cloud deployment, or staging and prod — all instances upload user files
to the **same** GCS bucket (`catdav-proxies`). The uploaded-clip object
key carries **no instance dimension**, so different instances silently
collide on the same blob and overwrite each other's media.

### Root cause (verified in code)

1. A user upload gets a *synthetic* clip id derived from the **local**
   SQLite auto-increment PK:
   `clip_id = UPLOAD_ID_BASE (1_000_000_000) + pk`
   (`backend/app/uploaded_ids.py`).
2. That id becomes the GCS object name directly:
   `gs://{bucket}/clips/{clip_id}.mov` (`backend/app/services/gcs.py:39`),
   with **no instance prefix**.
3. Each instance has its **own** SQLite DB (confirmed with the issue
   author). So instance A and instance B both compute PK=1 →
   `clip_id = 1_000_000_001` → the **same** blob
   `clips/1000000001.mov`. The second writer overwrites the first; the
   MD5 short-circuit in `upload_if_absent` only skips re-uploading
   *identical* bytes, so genuinely different uploads clobber each other.
4. The `ai_store_files` cache table keys on `(store_id, catdv_clip_id)`
   where `store_id = "gcs:{bucket_name}"` — again no instance dimension
   (`backend/migrations/0002_ai_store_files.sql`). Because the DB is
   per-instance this does not *leak rows* across instances, but it does
   mean a row can point at a blob whose bytes another instance has since
   overwritten.

### Why the UI listing is already isolated

Because each instance has its own SQLite DB, instance B never queries
instance A's `uploaded_clip` / `studio_set` rows. The *visibility* of
uploads in the UI is therefore already per-instance. The **only** real
leak is the shared GCS object namespace. This spec fixes exactly that.

## Answer to the issue's sub-question: how are sets & uploads managed in SQLite?

Documented here so the question is answered on the record. All three
tables live in the **per-instance** SQLite DB:

| Table | Migration | Purpose |
|---|---|---|
| `uploaded_clip` | `0018_uploaded_clips.sql` | One row per user-uploaded file: `original_filename`, `stored_filename`, `mime`, `size_bytes`, dimensions. PK drives the synthetic clip id. |
| `studio_set` | `0017_studio_sets.sql` | A named collection; `source IN ('archive','uploaded')`; unique on `(source, name)`. |
| `studio_set_clip` | `0017_studio_sets.sql` | Set membership: `(set_id, clip_id)`. `clip_id` may be a CatDV id or a synthetic uploaded id. |

A **set** is a metadata container; an **upload** is the actual file row
plus its GCS blob. Sets and their membership are instance-local already
and need **no change** for this work — only the GCS object key for
uploaded clips is shared and must be namespaced.

## Decision: Approach A — namespace only uploaded clips

Namespace **uploaded clips** by a mandatory `instance_id`; leave CatDV
clips shared.

- Uploaded clip → `instances/{instance_id}/uploads/{clip_id}.mov`
- CatDV clip → `clips/{clip_id}.mov` (**unchanged**)

### Alternatives considered

- **B — namespace every blob (incl. CatDV).** Simpler rule but every
  instance re-uploads the same *canonical* CatDV media, multiplying GCS
  storage/egress and discarding the legitimate cross-instance dedup.
  CatDV clip 12345 is the same media everywhere; sharing it is a feature,
  not a leak. Rejected.
- **C — one bucket per instance.** Full isolation but requires bucket
  provisioning + IAM per instance. A key prefix already gives complete
  isolation; a whole bucket is operational overkill. Rejected.

**Approach A** isolates exactly what is instance-local (synthetic-id
uploads) and preserves sharing of what is globally canonical (CatDV
clips).

## Design

### 1. Mandatory `instance_id` in settings

`backend/app/settings.py`:

- Add `instance_id: str` **with no default**. Pydantic then refuses to
  start if `INSTANCE_ID` is unset — the fail-fast "mandatory" guarantee
  requested in the issue. No accidental shared-namespace boot.
- Validate it is a non-empty slug matching `^[a-z0-9][a-z0-9-]*$` so it
  is safe inside a GCS object path (a Pydantic field validator that
  raises on mismatch).
- Wire the value in both environments:
  - local: `INSTANCE_ID` in `.env` (document in `.env.example` if present)
  - cloud: `INSTANCE_ID` in `deploy/cloudrun.env.yaml` (and staging's
    env if it has a separate one) — a distinct value per deployment.

### 2. Centralised, instance-aware blob naming in `GcsService`

`backend/app/services/gcs.py`:

- `GcsService.__init__` gains an `instance_id: str` parameter.
- Introduce one private `_blob_name(clip_id: int) -> str` that is the
  **single** place blob paths are constructed:

  ```python
  from backend.app.uploaded_ids import is_uploaded

  def _blob_name(self, clip_id: int) -> str:
      if is_uploaded(clip_id):
          return f"instances/{self._instance_id}/uploads/{clip_id}.mov"
      return f"clips/{clip_id}.mov"
  ```

- Route **every** existing path constructor through it: `gs_uri`,
  `upload_if_absent`, and any `get_blob` / `evict` / delete that builds
  `clips/{clip_id}.mov` today. After this change no string literal
  `clips/{...}.mov` remains outside `_blob_name`.
- `backend/app/context.py` (~line 612) passes `settings.instance_id`
  when constructing `GcsService(settings.gcs_bucket_name, settings.instance_id)`.

`is_uploaded` already lives in `backend/app/uploaded_ids.py` (a leaf
util), so importing it into `services/gcs.py` introduces no layering
problem.

### 3. One-time cache invalidation migration

New migration `backend/migrations/0021_uploads_instance_namespace.sql`
(next free number after `0020_enum_values.sql`):

```sql
-- Uploaded-clip AI-store cache rows point at the old shared
-- clips/{id}.mov path. Drop them so each instance re-uploads to its
-- namespaced path on the next Studio Run (cache-miss -> fetch is the
-- existing contract). Only cache entries are removed; uploaded_clip
-- rows and local file copies are untouched.
DELETE FROM ai_store_files WHERE catdv_clip_id >= 1000000000;
```

This is safe and idempotent: `ai_store_files` is a cache index, and the
annotator's `status() -> None` path already re-materialises a missing
upload. CatDV cache rows (`catdv_clip_id < 1_000_000_000`) are left
intact.

### 4. What is explicitly **out of scope**

- **Thumbnail cache** (`services/thumbnail_service.py`) and **proxy
  cache** (`services/proxy_resolver.py`) are local-filesystem, under
  `data_dir`, already per-instance. No change.
- **CatDV clip sharing** in GCS is intentionally preserved.
- **DB-level visibility filtering** is unnecessary: per-instance DBs
  already isolate listings. We do not add an instance column to any
  table.
- **Migrating existing orphaned blobs** at the old shared path is not
  attempted — they are ephemeral AI inputs that re-materialise on
  demand; a later GCS lifecycle/janitor pass can reap them.

## Components & data flow

```
upload POST  →  uploaded_clips_repo.create (local SQLite, per-instance)
             →  to_clip_id(pk)  = UPLOAD_ID_BASE + pk
             →  ai_store.ensure_uploaded(("uploaded", str(clip_id)), …)
             →  GcsService._blob_name(clip_id)
                   └─ is_uploaded ⇒ instances/{instance_id}/uploads/{clip_id}.mov
             →  ai_store_files.upsert(store_id, clip_id, gcs_uri=<namespaced>)

Studio Run   →  ai_store.status(clip_key)            (per-instance DB lookup)
             →  miss ⇒ ensure_uploaded ⇒ namespaced blob (as above)
```

Two instances now write to disjoint prefixes
(`instances/A/uploads/…` vs `instances/B/uploads/…`); no collision is
possible.

## Error handling

- Missing/blank/invalid `INSTANCE_ID` → app fails to start with a clear
  Pydantic validation error naming the field. (This is the intended
  safety behaviour, not a regression.)
- GCS write/read errors are unchanged — they flow through the existing
  `ai_store` / `humanise` paths.

## Testing

1. **Settings guard** — constructing `Settings` without `INSTANCE_ID`
   raises; a blank or non-slug value (`"A B"`, `"UPPER"`) raises; a valid
   slug (`"prod"`, `"local-pete"`) is accepted.
2. **`_blob_name` branching** — `is_uploaded` id →
   `instances/{id}/uploads/{clip_id}.mov`; CatDV id → `clips/{clip_id}.mov`.
   Parametrise with two distinct `instance_id`s and assert the prefixes
   differ for the same synthetic clip id (the collision is gone).
3. **No stray literal** — a guard test (grep-style, like the existing
   design-language guard) asserting `clips/` is built only inside
   `_blob_name` — optional but cheap; at minimum cover `gs_uri` and
   `upload_if_absent` return the namespaced value for uploaded ids.
4. **Migration** — apply migrations to a DB seeded with one CatDV
   (`catdv_clip_id = 42`) and one uploaded (`1000000001`) `ai_store_files`
   row; assert only the uploaded row is deleted.

## Manual acceptance flows

1. **Mandatory instance id fails fast.**
   *Setup:* a `.env` with `INSTANCE_ID` removed.
   *Action:* start the backend (`server-start`).
   *Expected:* the server refuses to boot with a validation error
   naming `instance_id` / `INSTANCE_ID`; no uvicorn listener appears on
   `8765`. Restore `INSTANCE_ID=local-dev` and it starts normally.

2. **Two instances no longer collide.**
   *Setup:* run instance A (`INSTANCE_ID=alpha`) and instance B
   (`INSTANCE_ID=beta`) against the same `catdav-proxies` bucket, each
   with its own SQLite DB.
   *Action:* on A, upload a clip and trigger a Studio Run so it lands in
   GCS; on B, upload a *different* file (which also gets synthetic id
   `1000000001`) and trigger a run.
   *Expected:* GCS shows two distinct objects
   `instances/alpha/uploads/1000000001.mov` and
   `instances/beta/uploads/1000000001.mov`; each instance's Gemini run
   reads back **its own** bytes. Neither overwrites the other.

3. **CatDV clips still shared (no regression).**
   *Setup:* two instances as above.
   *Action:* on A, run a real CatDV clip (e.g. id 12345) so it uploads to
   `clips/12345.mov`; on B, run the same CatDV clip.
   *Expected:* B's `ai_store.status()` for the CatDV clip is served from
   the shared blob (or re-uses `clips/12345.mov`); no
   `instances/.../clips/...` object is created for CatDV media.

4. **Existing uploaded-clip cache is invalidated cleanly.**
   *Setup:* an instance whose DB already has an `ai_store_files` row for
   an uploaded clip pointing at the old `clips/1000000001.mov`.
   *Action:* apply the new migration, then trigger a Studio Run for that
   clip.
   *Expected:* the stale row is gone; the run re-uploads to
   `instances/{id}/uploads/1000000001.mov` and completes; CatDV cache
   rows are untouched and still hit.
