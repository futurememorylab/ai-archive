"""Tests for the local-first clip-list filter resolver."""

from datetime import UTC, datetime

import pytest

from backend.app.archive.model import (
    CanonicalClip,
    MediaRef,
)
from backend.app.repositories.clip_list_cache import ClipListCacheRepo
from backend.app.services.clip_list_filters import (
    is_active,
    normalize_anno,
    normalize_cache,
    resolve,
)

PROVIDER = "catdv"
CATALOG = "881507"


def _clip(clip_id: str, *, name: str = "Clip") -> CanonicalClip:
    return CanonicalClip(
        key=(PROVIDER, clip_id),
        name=name,
        duration_secs=10.0,
        fps=25.0,
        markers=(),
        fields={},
        notes={},
        media=MediaRef(
            mime_type="video/quicktime",
            size_bytes=None,
            cached_path=None,
            upstream_handle=clip_id,
        ),
        provider_data={"ID": int(clip_id)},
        fetched_at=datetime(2026, 5, 22, tzinfo=UTC),
    )


async def _seed_list_cache(db, clip_ids: list[int]) -> None:
    repo = ClipListCacheRepo()
    items = tuple(_clip(str(i), name=f"Clip {i}") for i in clip_ids)
    await repo.upsert(
        db,
        provider_id=PROVIDER,
        catalog_id=CATALOG,
        query_text=None,
        offset=0,
        limit=50,
        total=len(items),
        items=items,
        fetched_at_iso="2026-05-22T00:00:00+00:00",
    )


async def _insert_proxy(db, clip_id: int) -> None:
    await db.execute(
        """
        INSERT INTO proxy_cache
          (catdv_clip_id, provider_id, provider_clip_id,
           file_path, size_bytes, etag, downloaded_at, last_used_at)
        VALUES (?, 'catdv', ?, ?, 1024, NULL, '2026-05-22', '2026-05-22')
        """,
        (clip_id, str(clip_id), f"/tmp/{clip_id}.mov"),
    )
    await db.commit()


async def _insert_ai_file(db, clip_id: int) -> None:
    await db.execute(
        """
        INSERT INTO ai_store_files
          (store_id, catdv_clip_id, provider_id, provider_clip_id,
           gcs_uri, mime_type, size_bytes, sha256, uploaded_at, last_used_at)
        VALUES ('gcs', ?, 'catdv', ?, 'gs://b/x', 'video/quicktime',
                2048, 'deadbeef', '2026-05-22', '2026-05-22')
        """,
        (clip_id, str(clip_id)),
    )
    await db.commit()


async def _seed_prompt_version(db) -> int:
    """Insert a minimal prompt + version so annotations.prompt_version_id FK holds."""
    await db.execute(
        """
        INSERT INTO prompts (id, name, description, created_at, updated_at)
        VALUES (1, 'p', NULL, '2026-05-22', '2026-05-22')
        """
    )
    await db.execute(
        """
        INSERT INTO prompt_versions
          (id, prompt_id, version_num, state, body, target_map,
           output_schema, model, created_at, updated_at)
        VALUES (1, 1, 1, 'production', 'b', '{}', '{}', 'gemini',
                '2026-05-22', '2026-05-22')
        """
    )
    await db.commit()
    return 1


async def _insert_annotation(db, clip_id: int) -> int:
    cur = await db.execute(
        """
        INSERT INTO annotations
          (catdv_clip_id, catdv_clip_name, prompt_version_id, job_id,
           model, prompt_used, raw_response, structured_output,
           clip_snapshot, created_at, provider_id, provider_clip_id)
        VALUES (?, ?, 1, NULL, 'gemini', 'prompt',
                '{}', '{}', '{}', '2026-05-22', 'catdv', ?)
        """,
        (clip_id, f"Clip {clip_id}", str(clip_id)),
    )
    await db.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


async def _insert_review_item(db, *, annotation_id: int, clip_id: int, applied: bool) -> None:
    await db.execute(
        """
        INSERT INTO review_items
          (annotation_id, catdv_clip_id, kind, target_identifier,
           proposed_value, edited_value, decision, decided_at, applied_at,
           provider_id, provider_clip_id)
        VALUES (?, ?, 'marker', NULL, '{}', NULL,
                'pending', NULL, ?, 'catdv', ?)
        """,
        (
            annotation_id,
            clip_id,
            "2026-05-22" if applied else None,
            str(clip_id),
        ),
    )
    await db.commit()


def test_normalize_helpers_clamp_unknown_values():
    assert normalize_cache(None) == "any"
    assert normalize_cache("garbage") == "any"
    assert normalize_cache("local") == "local"
    assert normalize_anno("for_review") == "for_review"
    assert normalize_anno(None) == "any"


def test_is_active():
    assert not is_active("any", "any")
    assert is_active("local", "any")
    assert is_active("any", "for_review")


@pytest.mark.asyncio
async def test_resolve_returns_none_when_no_filter(db):
    await _seed_list_cache(db, [1, 2, 3])
    out = await resolve(
        db,
        provider_id=PROVIDER,
        catalog_id=CATALOG,
        cache="any",
        anno="any",
    )
    assert out is None


@pytest.mark.asyncio
async def test_cache_local_filter(db):
    await _seed_list_cache(db, [1, 2, 3])
    await _insert_proxy(db, 2)
    out = await resolve(
        db,
        provider_id=PROVIDER,
        catalog_id=CATALOG,
        cache="local",
        anno="any",
    )
    assert out == {2}


@pytest.mark.asyncio
async def test_cache_ai_filter(db):
    await _seed_list_cache(db, [1, 2, 3])
    await _insert_ai_file(db, 3)
    out = await resolve(
        db,
        provider_id=PROVIDER,
        catalog_id=CATALOG,
        cache="ai",
        anno="any",
    )
    assert out == {3}


@pytest.mark.asyncio
async def test_cache_none_filter_excludes_both_layers(db):
    await _seed_list_cache(db, [1, 2, 3, 4])
    await _insert_proxy(db, 2)
    await _insert_ai_file(db, 3)
    out = await resolve(
        db,
        provider_id=PROVIDER,
        catalog_id=CATALOG,
        cache="none",
        anno="any",
    )
    assert out == {1, 4}


@pytest.mark.asyncio
async def test_anno_for_review_filter(db):
    await _seed_list_cache(db, [1, 2, 3])
    await _seed_prompt_version(db)
    a1 = await _insert_annotation(db, 1)
    await _insert_review_item(db, annotation_id=a1, clip_id=1, applied=False)
    a2 = await _insert_annotation(db, 2)
    await _insert_review_item(db, annotation_id=a2, clip_id=2, applied=True)
    out = await resolve(
        db,
        provider_id=PROVIDER,
        catalog_id=CATALOG,
        cache="any",
        anno="for_review",
    )
    assert out == {1}


@pytest.mark.asyncio
async def test_anno_applied_filter(db):
    await _seed_list_cache(db, [1, 2, 3])
    await _seed_prompt_version(db)
    a1 = await _insert_annotation(db, 1)
    await _insert_review_item(db, annotation_id=a1, clip_id=1, applied=False)
    a2 = await _insert_annotation(db, 2)
    await _insert_review_item(db, annotation_id=a2, clip_id=2, applied=True)
    out = await resolve(
        db,
        provider_id=PROVIDER,
        catalog_id=CATALOG,
        cache="any",
        anno="applied",
    )
    assert out == {2}


@pytest.mark.asyncio
async def test_anno_none_filter(db):
    await _seed_list_cache(db, [1, 2, 3])
    await _seed_prompt_version(db)
    await _insert_annotation(db, 2)
    out = await resolve(
        db,
        provider_id=PROVIDER,
        catalog_id=CATALOG,
        cache="any",
        anno="none",
    )
    assert out == {1, 3}


@pytest.mark.asyncio
async def test_anno_has_any_filter(db):
    await _seed_list_cache(db, [1, 2, 3])
    await _seed_prompt_version(db)
    await _insert_annotation(db, 1)
    await _insert_annotation(db, 3)
    out = await resolve(
        db,
        provider_id=PROVIDER,
        catalog_id=CATALOG,
        cache="any",
        anno="has_any",
    )
    assert out == {1, 3}


@pytest.mark.asyncio
async def test_intersection_of_cache_and_anno(db):
    await _seed_list_cache(db, [1, 2, 3, 4])
    await _seed_prompt_version(db)
    # clip 2 has both local cache and a for-review draft → only one to match
    await _insert_proxy(db, 2)
    a2 = await _insert_annotation(db, 2)
    await _insert_review_item(db, annotation_id=a2, clip_id=2, applied=False)
    # clip 3 has cache only
    await _insert_proxy(db, 3)
    # clip 4 has a draft only
    a4 = await _insert_annotation(db, 4)
    await _insert_review_item(db, annotation_id=a4, clip_id=4, applied=False)

    out = await resolve(
        db,
        provider_id=PROVIDER,
        catalog_id=CATALOG,
        cache="local",
        anno="for_review",
    )
    assert out == {2}


# ---------------------------------------------------------------------------
# Batch (job) filter tests
# ---------------------------------------------------------------------------

async def _insert_job(db, *, job_id: int) -> None:
    """Insert a minimal job row (prompt_version_id=1 must already exist)."""
    await db.execute(
        """
        INSERT INTO jobs (id, prompt_version_id, status, created_at, total_clips)
        VALUES (?, 1, 'completed', '2026-05-22', 1)
        """,
        (job_id,),
    )
    await db.commit()


async def _insert_job_item(db, *, job_id: int, clip_id: int, status: str = "pending") -> None:
    await db.execute(
        "INSERT INTO job_items (job_id, catdv_clip_id, status) VALUES (?, ?, ?)",
        (job_id, clip_id, status),
    )
    await db.commit()


async def _insert_annotation_with_job(db, clip_id: int, job_id: int) -> int:
    cur = await db.execute(
        """
        INSERT INTO annotations
          (catdv_clip_id, catdv_clip_name, prompt_version_id, job_id,
           model, prompt_used, raw_response, structured_output,
           clip_snapshot, created_at, provider_id, provider_clip_id)
        VALUES (?, ?, 1, ?, 'gemini', 'prompt',
                '{}', '{}', '{}', '2026-05-22', 'catdv', ?)
        """,
        (clip_id, f"Clip {clip_id}", job_id, str(clip_id)),
    )
    await db.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


def test_is_active_batch():
    assert not is_active("any", "any", None)
    assert is_active("any", "any", 5)
    assert is_active("local", "any", None)


@pytest.mark.asyncio
async def test_resolve_batch_filter_isolates_job(db):
    """resolve with batch= returns every clip in that job (all job_items),
    regardless of per-item status, and excludes clips from other jobs."""
    await _seed_list_cache(db, [1, 2, 3])
    await _seed_prompt_version(db)
    await _insert_job(db, job_id=10)
    await _insert_job(db, job_id=20)

    # Job 10 operates on clips 1 and 3 (1 still pending, 3 already done).
    await _insert_job_item(db, job_id=10, clip_id=1, status="pending")
    await _insert_job_item(db, job_id=10, clip_id=3, status="review_ready")
    # Job 20 operates on clip 2 — must not leak into the job-10 view.
    await _insert_job_item(db, job_id=20, clip_id=2, status="pending")

    out = await resolve(
        db,
        provider_id=PROVIDER,
        catalog_id=CATALOG,
        cache="any",
        anno="any",
        batch=10,
    )
    assert out == {1, 3}


@pytest.mark.asyncio
async def test_resolve_batch_intersects_with_cache_filter(db):
    """batch AND cache filters intersect — only clips matching both are returned."""
    await _seed_list_cache(db, [1, 2, 3])
    await _seed_prompt_version(db)
    await _insert_job(db, job_id=10)

    # Both clips 1 and 2 are in job 10
    await _insert_job_item(db, job_id=10, clip_id=1, status="pending")
    await _insert_job_item(db, job_id=10, clip_id=2, status="pending")

    # Only clip 2 has a local proxy — intersection should return only clip 2
    await _insert_proxy(db, 2)

    out = await resolve(
        db,
        provider_id=PROVIDER,
        catalog_id=CATALOG,
        cache="local",
        anno="any",
        batch=10,
    )
    assert out == {2}


@pytest.mark.asyncio
async def test_resolve_batch_returns_none_when_no_filter(db):
    """With batch=None and no other filters, resolve still returns None."""
    await _seed_list_cache(db, [1, 2])
    out = await resolve(
        db,
        provider_id=PROVIDER,
        catalog_id=CATALOG,
        cache="any",
        anno="any",
        batch=None,
    )
    assert out is None
