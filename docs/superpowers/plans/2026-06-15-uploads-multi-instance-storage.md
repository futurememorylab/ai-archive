# Uploads multi-instance storage — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop different app instances from overwriting each other's uploaded-clip media in the shared GCS bucket by namespacing uploaded-clip object keys with a mandatory `INSTANCE_ID`.

**Architecture:** Add a mandatory, slug-validated `instance_id` to `Settings` (fail-fast if unset). Centralise GCS blob naming in `GcsService._blob_name()` so uploaded clips (synthetic ids `>= UPLOAD_ID_BASE`) go to `instances/{instance_id}/uploads/{clip_id}.mov` while canonical CatDV clips stay at the shared `clips/{clip_id}.mov`. A one-time migration drops stale uploaded-clip cache rows so they re-materialise at the namespaced path. Each instance already owns its SQLite DB, so listing/visibility is already isolated — only the GCS object namespace needs fixing.

**Tech Stack:** Python 3.12/3.13, FastAPI, pydantic-settings, google-cloud-storage, SQLite (aiosqlite), pytest.

**Spec:** `docs/superpowers/specs/2026-06-15-uploads-multi-instance-storage-design.md`

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `backend/app/settings.py` | Modify | Add mandatory `instance_id: str` + slug validator |
| `backend/app/services/gcs.py` | Modify | Add `instance_id` ctor param + `_blob_name()`; route `gs_uri`/`upload_if_absent`/`delete` through it |
| `backend/app/context.py` | Modify | Pass `settings.instance_id` into `GcsService(...)` |
| `backend/migrations/0021_uploads_instance_namespace.sql` | Create | Drop uploaded-clip `ai_store_files` cache rows |
| `tests/conftest.py` | Modify | Add `INSTANCE_ID` to the shared test-env defaults |
| `tests/unit/test_settings.py` (or new `test_settings_instance_id.py`) | Create/Modify | Guard mandatory + slug validation |
| `tests/unit/test_settings_pure_env.py` | Modify | Include `INSTANCE_ID` in required pure-env set |
| `tests/unit/test_gcs.py` | Modify | Set `_instance_id` on hand-built services; add namespacing tests |
| `tests/unit/test_migration_0021_uploads_instance_namespace.py` | Create | Assert only uploaded rows deleted |
| `.env` | Modify | Add `INSTANCE_ID=local-dev` |
| `.env.example` | Modify | Document `INSTANCE_ID` |
| `deploy/cloudrun.env.yaml` | Modify | Add `INSTANCE_ID: "prod"` |
| `deploy/staging.env.yaml` | Modify | Add `INSTANCE_ID: "staging"` |
| `docs/DEPLOY.md` | Modify | Document the mandatory var + per-deployment values |
| `deploy/README.md` | Modify | Add `INSTANCE_ID` to the local-vs-cloud matrix |

---

## Task 1: Make the test suite tolerate a new mandatory setting

The shared autouse fixture in `tests/conftest.py` populates required Settings env vars so unrelated tests don't fail at import. Adding a mandatory `INSTANCE_ID` first (before it exists in `Settings`) keeps the suite green as we add the field.

**Files:**
- Modify: `tests/conftest.py:36-45`

- [ ] **Step 1: Add `INSTANCE_ID` to the shared test-env defaults**

In `tests/conftest.py`, add the key to `_TEST_ENV_DEFAULTS`:

```python
_TEST_ENV_DEFAULTS = {
    "APP_ENV": "dev",
    "CATDV_OFFLINE": "true",
    "CATDV_BASE_URL": "http://localhost:0",
    "CATDV_USERNAME": "",
    "CATDV_PASSWORD": "",
    "CATDV_CATALOG_ID": "881507",
    "GCP_PROJECT_ID": "test-project",
    "GCS_BUCKET_NAME": "test-bucket",
    "INSTANCE_ID": "test-instance",
}
```

- [ ] **Step 2: Commit**

```bash
git add tests/conftest.py
git commit -m "test(#55): seed INSTANCE_ID in shared test-env defaults"
```

---

## Task 2: Add the mandatory, validated `instance_id` setting

**Files:**
- Modify: `backend/app/settings.py:7` (import), `backend/app/settings.py:21-31` (field placement)
- Create: `tests/unit/test_settings_instance_id.py`
- Modify: `tests/unit/test_settings_pure_env.py:9-23`

- [ ] **Step 1: Write the failing guard test**

Create `tests/unit/test_settings_instance_id.py`:

```python
"""INSTANCE_ID is mandatory and slug-validated. It namespaces uploaded-clip
GCS keys so two app instances sharing one bucket cannot overwrite each
other's media (issue #55)."""

import os

import pytest
from pydantic import ValidationError

from backend.app.settings import Settings

_REQUIRED = {
    "CATDV_BASE_URL": "http://127.0.0.1:18080",
    "CATDV_CATALOG_ID": "881507",
    "GCP_PROJECT_ID": "catdav",
    "GCS_BUCKET_NAME": "catdav-proxies",
}


def _clean_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # no .env in cwd
    for key in list(os.environ):
        if key.startswith(("CATDV_", "GCP_", "GCS_", "APP_", "DATA_", "GOOGLE_", "INSTANCE_")):
            monkeypatch.delenv(key, raising=False)
    for key, value in _REQUIRED.items():
        monkeypatch.setenv(key, value)


def test_instance_id_required(monkeypatch, tmp_path):
    _clean_env(monkeypatch, tmp_path)
    # INSTANCE_ID deliberately unset
    with pytest.raises(ValidationError):
        Settings()


def test_instance_id_accepts_slug(monkeypatch, tmp_path):
    _clean_env(monkeypatch, tmp_path)
    monkeypatch.setenv("INSTANCE_ID", "local-pete")
    assert Settings().instance_id == "local-pete"


@pytest.mark.parametrize("bad", ["", "Has Space", "UPPER", "under_score", "-leading"])
def test_instance_id_rejects_non_slug(monkeypatch, tmp_path, bad):
    _clean_env(monkeypatch, tmp_path)
    monkeypatch.setenv("INSTANCE_ID", bad)
    with pytest.raises(ValidationError):
        Settings()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_settings_instance_id.py -v`
Expected: FAIL — `Settings()` currently has no `instance_id`, so the slug tests fail (no validation) and the "required" test fails (no error raised).

- [ ] **Step 3: Add the field + validator to `Settings`**

In `backend/app/settings.py`, the import line already reads:

```python
from pydantic import Field, SecretStr, model_validator
```

Change it to also import `field_validator`:

```python
from pydantic import Field, SecretStr, field_validator, model_validator
```

Then add the mandatory field next to the other required identity fields (just after `data_dir`, around line 20):

```python
    data_dir: Path = Field(default=Path("./data"))

    # Mandatory per-deployment identifier. Namespaces uploaded-clip GCS
    # object keys (instances/{instance_id}/uploads/{clip_id}.mov) so two
    # instances sharing one bucket cannot overwrite each other's uploads
    # (issue #55). No default -> the app refuses to boot if it is unset.
    instance_id: str
```

Add the validator method inside the `Settings` class (place it near the other validators / after the field declarations):

```python
    @field_validator("instance_id")
    @classmethod
    def _instance_id_is_slug(cls, v: str) -> str:
        import re

        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", v):
            raise ValueError(
                "INSTANCE_ID must be a lowercase slug matching "
                "[a-z0-9][a-z0-9-]* (e.g. 'prod', 'staging', 'local-pete')"
            )
        return v
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_settings_instance_id.py -v`
Expected: PASS (all parametrised cases).

- [ ] **Step 5: Keep the pure-env guard green**

`tests/unit/test_settings_pure_env.py` builds `Settings()` from a cleaned env and will now fail (no `INSTANCE_ID`). Update it. Add `INSTANCE_ID` to `_REQUIRED` and include the `INSTANCE_` prefix in the delenv loop:

```python
_REQUIRED = {
    "CATDV_BASE_URL": "http://127.0.0.1:18080",
    "CATDV_CATALOG_ID": "881507",
    "GCP_PROJECT_ID": "catdav",
    "GCS_BUCKET_NAME": "catdav-proxies",
    "INSTANCE_ID": "prod",
}
```

And in `test_settings_resolve_from_pure_env`, extend the prefix tuple:

```python
    for key in list(os.environ):
        if key.startswith(("CATDV_", "GCP_", "GCS_", "APP_", "DATA_", "GOOGLE_", "INSTANCE_")):
            monkeypatch.delenv(key, raising=False)
```

Add an assertion at the end of `test_settings_resolve_from_pure_env`:

```python
    assert s.instance_id == "prod"
```

- [ ] **Step 6: Run the settings suite**

Run: `.venv/bin/python -m pytest tests/unit/ -k settings -v`
Expected: PASS (all settings tests, including the pre-existing ones that rely on the conftest default).

- [ ] **Step 7: Commit**

```bash
git add backend/app/settings.py tests/unit/test_settings_instance_id.py tests/unit/test_settings_pure_env.py
git commit -m "feat(#55): mandatory slug-validated INSTANCE_ID setting"
```

---

## Task 3: Namespace uploaded-clip GCS object keys

`GcsService` builds `clips/{clip_id}.mov` in three places (`gs_uri`, `upload_if_absent`, `delete`). Centralise that into `_blob_name()` and branch on `is_uploaded(clip_id)`.

**Files:**
- Modify: `backend/app/services/gcs.py:26-57`
- Modify: `tests/unit/test_gcs.py`

- [ ] **Step 1: Write the failing namespacing tests**

Add to `tests/unit/test_gcs.py` (the file builds services via `GcsService.__new__` and sets `_bucket`; these new tests also set `_instance_id`):

```python
from backend.app.uploaded_ids import UPLOAD_ID_BASE


def _service(instance_id="alpha"):
    bucket = MagicMock(); bucket.name = "test-bucket"
    s = GcsService.__new__(GcsService)
    s._bucket = bucket
    s._instance_id = instance_id
    return s, bucket


def test_blob_name_catdv_clip_is_shared():
    s, _ = _service()
    assert s._blob_name(42) == "clips/42.mov"


def test_blob_name_uploaded_clip_is_instance_namespaced():
    s, _ = _service(instance_id="alpha")
    up = UPLOAD_ID_BASE + 1
    assert s._blob_name(up) == f"instances/alpha/uploads/{up}.mov"


def test_uploaded_clip_keys_differ_across_instances():
    up = UPLOAD_ID_BASE + 1  # same synthetic id on both instances
    a, _ = _service(instance_id="alpha")
    b, _ = _service(instance_id="beta")
    assert a._blob_name(up) != b._blob_name(up)


def test_gs_uri_namespaces_uploaded_clip():
    s, _ = _service(instance_id="alpha")
    up = UPLOAD_ID_BASE + 7
    assert s.gs_uri(up) == f"gs://test-bucket/instances/alpha/uploads/{up}.mov"


def test_upload_if_absent_uploads_uploaded_clip_to_namespaced_path(tmp_path: Path):
    local = tmp_path / "f.mov"; local.write_bytes(b"data")
    s, bucket = _service(instance_id="alpha")
    bucket.get_blob.return_value = None
    blob = MagicMock(); bucket.blob.return_value = blob
    up = UPLOAD_ID_BASE + 3
    uri = s.upload_if_absent(clip_id=up, local_path=local, mime="video/mp4")
    assert uri == f"gs://test-bucket/instances/alpha/uploads/{up}.mov"
    bucket.blob.assert_called_with(
        f"instances/alpha/uploads/{up}.mov", chunk_size=8 * 1024 * 1024
    )


def test_delete_uploaded_clip_targets_namespaced_path():
    s, bucket = _service(instance_id="alpha")
    blob = MagicMock(); bucket.blob.return_value = blob
    up = UPLOAD_ID_BASE + 9
    s.delete(clip_id=up)
    bucket.blob.assert_called_with(f"instances/alpha/uploads/{up}.mov")
    blob.delete.assert_called_once()
```

- [ ] **Step 2: Update the existing hand-built services to set `_instance_id`**

The pre-existing tests in `tests/unit/test_gcs.py` build `GcsService.__new__(GcsService)` and set only `service._bucket`. `_blob_name` reads `self._instance_id`, but those tests use CatDV ids (42, 7) that never touch `_instance_id` — they stay on the `clips/...` branch, so they keep working unchanged. No edit needed to the CatDV-id tests; leave them as-is.

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_gcs.py -v`
Expected: FAIL on the new tests — `_blob_name` does not exist and `gs_uri`/`upload_if_absent`/`delete` still hard-code `clips/...`.

- [ ] **Step 4: Implement `_blob_name` and route the three methods through it**

In `backend/app/services/gcs.py`, update the imports near the top:

```python
import base64
import hashlib
from datetime import timedelta
from pathlib import Path

import google.auth
import google.auth.transport.requests
from google.cloud import storage  # type: ignore[import-not-found]

from backend.app.uploaded_ids import is_uploaded
```

Update `__init__` to accept and store `instance_id`:

```python
    def __init__(self, bucket_name: str, instance_id: str) -> None:
        self._client = storage.Client()
        self._bucket = self._client.bucket(bucket_name)
        self._instance_id = instance_id
```

Add `_blob_name` and route `gs_uri`, `upload_if_absent`, and `delete` through it:

```python
    def _blob_name(self, clip_id: int) -> str:
        # Uploaded clips have a synthetic id derived from a *local* SQLite
        # PK, so two instances collide on the same id. Namespace them per
        # instance. CatDV clips are globally canonical -> keep them shared
        # (cross-instance dedup). See issue #55 / the design spec.
        if is_uploaded(clip_id):
            return f"instances/{self._instance_id}/uploads/{clip_id}.mov"
        return f"clips/{clip_id}.mov"

    def gs_uri(self, clip_id: int) -> str:
        return f"gs://{self._bucket.name}/{self._blob_name(clip_id)}"

    def upload_if_absent(self, clip_id: int, local_path: Path, mime: str) -> str:
        blob_name = self._blob_name(clip_id)
        # Blob names are keyed on clip_id (instance-namespaced for uploads),
        # and a stale/orphan blob can outlive its DB row. Presence alone is
        # NOT proof of content: re-uploading a reused clip_id with different
        # bytes must overwrite, or playback silently serves stale media.
        # Compare the stored md5 (a metadata read, no download) and only
        # skip the upload when the content already matches.
        existing = self._bucket.get_blob(blob_name)
        if existing is None or existing.md5_hash != _local_md5_b64(local_path):
            # Setting chunk_size flips upload_from_filename into resumable
            # mode, so a slow multi-hundred-MB upload isn't bounded by the
            # default 120s single-shot timeout.
            blob = self._bucket.blob(blob_name, chunk_size=_HASH_CHUNK)
            blob.upload_from_filename(str(local_path), content_type=mime, timeout=1800)
        return f"gs://{self._bucket.name}/{blob_name}"

    def delete(self, clip_id: int) -> None:
        blob = self._bucket.blob(self._blob_name(clip_id))
        blob.delete()
```

Leave `thumb_uri` / `download_thumb` / `upload_thumb` unchanged — thumbnails are not on the uploaded-clip GCS path (the thumbnail service fetches from CatDV to local disk), so they are out of scope per the spec.

- [ ] **Step 5: Run the GCS suite to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_gcs.py -v`
Expected: PASS (existing CatDV-id tests still produce `clips/42.mov`; new uploaded-id tests namespace correctly).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/gcs.py tests/unit/test_gcs.py
git commit -m "feat(#55): namespace uploaded-clip GCS keys by instance_id"
```

---

## Task 4: Wire `instance_id` into `GcsService` construction

**Files:**
- Modify: `backend/app/context.py` (the `GcsService(...)` call, ~line 612)

- [ ] **Step 1: Find the construction site**

Run: `grep -rn "GcsService(" backend/app`
Expected: a single non-test construction site in `backend/app/context.py` (around line 612: `gcs_service = GcsService(settings.gcs_bucket_name)`). If more than one appears, update each.

- [ ] **Step 2: Pass `settings.instance_id`**

Change the call in `backend/app/context.py`:

```python
    gcs_service = GcsService(settings.gcs_bucket_name, settings.instance_id)
```

- [ ] **Step 3: Verify nothing else constructs `GcsService` positionally without the new arg**

Run: `grep -rn "GcsService(" backend/`
Expected: every non-test call now passes two args. (Tests use `GcsService.__new__`, which bypasses `__init__`, so they are unaffected.)

- [ ] **Step 4: Run the broader suite for boot/context regressions**

Run: `.venv/bin/python -m pytest tests/unit/test_gcs.py tests/integration/test_context_boot_recovery.py tests/integration/test_context_manual_boot.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/context.py
git commit -m "feat(#55): pass instance_id into GcsService construction"
```

---

## Task 5: One-time cache invalidation migration

Existing `ai_store_files` rows for uploaded clips still point at the old shared `clips/{id}.mov` path. Drop them so the next Studio Run re-uploads to the namespaced path (cache-miss → fetch is the existing contract). CatDV cache rows are untouched.

**Files:**
- Create: `backend/migrations/0021_uploads_instance_namespace.sql`
- Create: `tests/unit/test_migration_0021_uploads_instance_namespace.py`

- [ ] **Step 1: Write the failing migration test**

Create `tests/unit/test_migration_0021_uploads_instance_namespace.py`:

```python
"""0021 drops only uploaded-clip ai_store_files cache rows (synthetic ids
>= UPLOAD_ID_BASE) so they re-upload to the instance-namespaced GCS path.
CatDV cache rows (< UPLOAD_ID_BASE) survive (issue #55)."""

from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.uploaded_ids import UPLOAD_ID_BASE


@pytest.fixture
async def conn(tmp_path: Path):
    cm = open_db(tmp_path / "t.db")
    c = await cm.__aenter__()
    yield c
    await cm.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_only_uploaded_rows_deleted(conn):
    await apply_migrations(conn, Path("backend/migrations"))
    catdv_id = 42
    uploaded_id = UPLOAD_ID_BASE + 1
    for clip_id in (catdv_id, uploaded_id):
        await conn.execute(
            "INSERT INTO ai_store_files (store_id, catdv_clip_id, gcs_uri, "
            "mime_type, size_bytes, sha256, uploaded_at, last_used_at) "
            "VALUES ('gcs:test-bucket', ?, 'gs://test-bucket/clips/x.mov', "
            "'video/mp4', 1, 'h', 't', 't')",
            (clip_id,),
        )
    await conn.commit()

    # Re-run the 0021 statement directly (migrations are idempotent by
    # version; re-applying the DELETE proves the intended scope).
    await conn.execute(
        "DELETE FROM ai_store_files WHERE catdv_clip_id >= ?", (UPLOAD_ID_BASE,)
    )
    await conn.commit()

    cur = await conn.execute("SELECT catdv_clip_id FROM ai_store_files")
    remaining = {row[0] for row in await cur.fetchall()}
    assert catdv_id in remaining
    assert uploaded_id not in remaining
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_migration_0021_uploads_instance_namespace.py -v`
Expected: FAIL — `0021_*.sql` does not exist yet, so `apply_migrations` won't know it (the test still constructs rows, but the point is to add the migration; run after creating it to confirm green). If `apply_migrations` errors on a missing file it won't; it globs the dir — so this first run mainly proves the table/insert shape before the file lands.

- [ ] **Step 3: Create the migration file**

Create `backend/migrations/0021_uploads_instance_namespace.sql`:

```sql
-- Issue #55: uploaded-clip GCS object keys are now namespaced per
-- instance (instances/{instance_id}/uploads/{clip_id}.mov). Existing
-- ai_store_files cache rows for uploaded clips still point at the old
-- shared clips/{id}.mov path, so drop them; the next Studio Run
-- re-uploads to the namespaced path (cache-miss -> fetch is the existing
-- contract). Only cache index rows are removed -- uploaded_clip rows and
-- local file copies are untouched. CatDV cache rows (< 1000000000) survive.
DELETE FROM ai_store_files WHERE catdv_clip_id >= 1000000000;
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_migration_0021_uploads_instance_namespace.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/migrations/0021_uploads_instance_namespace.sql tests/unit/test_migration_0021_uploads_instance_namespace.py
git commit -m "feat(#55): migration drops stale uploaded-clip cache rows"
```

---

## Task 6: Local env config (`.env`, `.env.example`)

**Files:**
- Modify: `.env`
- Modify: `.env.example`

- [ ] **Step 1: Add `INSTANCE_ID` to the local `.env`**

Add a line to `.env` (near the top App section, after `DATA_DIR`):

```
# Per-deployment identifier (mandatory). Namespaces uploaded-clip GCS keys.
INSTANCE_ID=local-dev
```

- [ ] **Step 2: Document it in `.env.example`**

In `.env.example`, under the `# App` block (after `DATA_DIR=./data`), add:

```
# Per-deployment identifier — MANDATORY, lowercase slug ([a-z0-9-]).
# Namespaces uploaded-clip media in the shared GCS bucket so instances
# never overwrite each other (issue #55). Use a distinct value per place
# the app runs: local-dev, staging, prod. The app refuses to boot if unset.
INSTANCE_ID=local-dev
```

- [ ] **Step 3: Confirm the app still boots locally (sanity, optional)**

Run: `.venv/bin/python -c "from backend.app.settings import Settings; print(Settings().instance_id)"`
Expected: prints `local-dev` (reads from `.env`). If it raises, `.env` is missing the line.

- [ ] **Step 4: Commit**

```bash
git add .env.example
git commit -m "docs(#55): document mandatory INSTANCE_ID in .env.example"
```

Note: `.env` is git-ignored (developer-local) — Step 1 is a working-tree change only and is intentionally not committed. Mention in the PR that each developer must add `INSTANCE_ID` to their local `.env`.

---

## Task 7: Cloud Run env config (prod + staging)

**Files:**
- Modify: `deploy/cloudrun.env.yaml`
- Modify: `deploy/staging.env.yaml`

- [ ] **Step 1: Add `INSTANCE_ID` to prod**

In `deploy/cloudrun.env.yaml`, add near the other identity vars (e.g. after `APP_ENV: "prod"`):

```yaml
# Per-deployment identifier (mandatory; issue #55). Namespaces
# uploaded-clip GCS keys so prod never collides with staging/local in the
# shared catdav-proxies bucket. Must be unique per Cloud Run service.
INSTANCE_ID: "prod"
```

- [ ] **Step 2: Add `INSTANCE_ID` to staging**

In `deploy/staging.env.yaml`, add (after `APP_ENV: "prod"`):

```yaml
# Per-deployment identifier (mandatory; issue #55). MUST differ from prod
# ("prod") so staging uploads land under instances/staging/ and never
# overwrite prod media in the shared catdv-proxies bucket.
INSTANCE_ID: "staging"
```

- [ ] **Step 3: Verify both files parse as YAML**

Run: `.venv/bin/python -c "import yaml,sys; [print(f, yaml.safe_load(open(f))['INSTANCE_ID']) for f in ['deploy/cloudrun.env.yaml','deploy/staging.env.yaml']]"`
Expected: prints `deploy/cloudrun.env.yaml prod` and `deploy/staging.env.yaml staging`.

- [ ] **Step 4: Commit**

```bash
git add deploy/cloudrun.env.yaml deploy/staging.env.yaml
git commit -m "feat(#55): set INSTANCE_ID for prod and staging Cloud Run"
```

---

## Task 8: Deployment documentation

**Files:**
- Modify: `docs/DEPLOY.md`
- Modify: `deploy/README.md`

- [ ] **Step 1: Document the mandatory var in `docs/DEPLOY.md`**

In `docs/DEPLOY.md`, in the Dev (Mac) `.env` edit block (around line 21, where it says "Edit .env: at minimum set CATDV_PASSWORD and GOOGLE_APPLICATION_CREDENTIALS"), extend it to mention `INSTANCE_ID`:

```
# Edit .env: at minimum set CATDV_PASSWORD, GOOGLE_APPLICATION_CREDENTIALS,
# and INSTANCE_ID (mandatory; a lowercase slug unique to this machine,
# e.g. local-<yourname>). It namespaces uploaded-clip media in the shared
# GCS bucket so instances never overwrite each other (issue #55). The app
# refuses to boot if INSTANCE_ID is unset.
```

And in the Prod (CatDV server) `.env` block (around line 87-94), add a line to the listed settings:

```
#   INSTANCE_ID=<unique-slug>   # mandatory; e.g. "prod" — namespaces uploads (issue #55)
```

- [ ] **Step 2: Add `INSTANCE_ID` to the local-vs-cloud matrix in `deploy/README.md`**

Open `deploy/README.md`, find the section that lists the environment variables / the local-vs-cloud matrix, and add a row/entry for `INSTANCE_ID`:

```
- INSTANCE_ID — MANDATORY everywhere (local, staging, prod). A lowercase
  slug ([a-z0-9-]) unique per running instance. Namespaces uploaded-clip
  GCS object keys (instances/{INSTANCE_ID}/uploads/{clip_id}.mov) so two
  instances sharing the catdv-proxies bucket cannot overwrite each other's
  uploads. CatDV clips stay shared at clips/{clip_id}.mov. Values in use:
  local=local-dev / per-dev, staging=staging, prod=prod. The app fails to
  boot if it is unset (Settings validation). See issue #55 and
  docs/superpowers/specs/2026-06-15-uploads-multi-instance-storage-design.md.
```

(Match the surrounding formatting — if the matrix is a table, add a table row instead; if it's the WG/Litestream-style bullet list near the cloud-only vars, add a bullet there. `INSTANCE_ID` is *not* cloud-only — call that out, since it differs from `WG_*`/`LITESTREAM_*`.)

- [ ] **Step 3: Commit**

```bash
git add docs/DEPLOY.md deploy/README.md
git commit -m "docs(#55): document mandatory INSTANCE_ID for all environments"
```

---

## Task 9: Full suite + lint verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS. Pay attention to any test that constructs `Settings()` outside the conftest fixture (Task 1 covers the shared default; the pure-env guard is covered in Task 2). If any test fails for a missing `INSTANCE_ID`, set it in that test's local env helper the same way (`monkeypatch.setenv("INSTANCE_ID", "test-instance")`).

- [ ] **Step 2: Run the import-linter and design guards**

Run: `.venv/bin/lint-imports` and `.venv/bin/python -m pytest tests/unit/test_design_language_guard.py -q`
Expected: PASS. (`services/gcs.py` importing `uploaded_ids` is a leaf→leaf import and breaks no contract.)

- [ ] **Step 3: Confirm no stray hard-coded uploaded-clip key remains**

Run: `grep -rn "clips/" backend/app | grep -v "_blob_name\|thumbs/"`
Expected: the only matches are inside `services/gcs.py` `_blob_name` (the CatDV branch) — no other code constructs `clips/{...}.mov` for uploaded clips.

- [ ] **Step 4: Commit any fixups, then stop**

```bash
git add -A
git commit -m "test(#55): green full suite + guards" || echo "nothing to commit"
```

---

## Self-Review notes (for the implementer)

- **Spec coverage:** mandatory `INSTANCE_ID` (Task 2), uploaded-vs-CatDV namespacing in one `_blob_name` (Task 3), construction wiring (Task 4), cache-invalidation migration `0021` (Task 5), `.env`/`.env.example` (Task 6), prod+staging Cloud Run (Task 7), DEPLOY.md + deploy/README.md (Task 8). The sets/uploads SQLite answer is documentation-only (in the spec) and needs no code.
- **Out of scope (do not touch):** thumbnail cache, proxy cache, CatDV clip sharing, any DB visibility column.
- **Watch:** the mandatory field ripples into every `Settings()` construction. Task 1 seeds the shared conftest default; Task 9 Step 1 sweeps for stragglers.
