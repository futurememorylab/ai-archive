"""T3-C3: pin the clips-list page against N+1 query regressions.

The clips-list render (GET /) must issue a **constant** number of SQL
statements regardless of how many clips are on the page.  The route already
achieves this (pagination-bounded page → 1 chunked_in_clause batch per layer
→ constant query count); this test pins that fact so a future per-row DB
lookup inside the render loop will trip CI.

Query inventory (no filters, no batch, all bounded by page size):
  1. clip_list_cache_repo.get(…) — one row lookup (list-page cache)
  2. cache_inspector.status_for_clips — 5 batched queries (metadata /
     media-local / media-ai / pins / pending-counts), all chunked_in_clause
  3. jobs_repo.list_jobs — one query
  4. review_items_repo.list_pending_clips — one query
  ─────────────────────────────────────────────────────────────────────────
  Total = 8 statements; none grow with clip count (page capped at limit=10,
  well inside one 400-key chunk).
"""

from __future__ import annotations

import asyncio
import importlib
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from backend.app.archive.errors import ProviderError
from backend.app.archive.model import (
    CanonicalClip,
    ClipPage,
    ClipQuery,
    MediaRef,
)
from tests._helpers.live_ctx import install_live_ctx

# ---------------------------------------------------------------------------
# Test environment setup (mirrors test_cache_page_perf.py pattern)
# ---------------------------------------------------------------------------


def _setenv(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))


def _make_app(monkeypatch, tmp_path):
    _setenv(monkeypatch, tmp_path)
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    return main_mod.app


# ---------------------------------------------------------------------------
# Helpers: fake archive and canonical clip builder
# ---------------------------------------------------------------------------


def _canonical(clip_id: int) -> CanonicalClip:
    """Minimal CanonicalClip for the given id."""
    now = datetime.now(UTC)
    return CanonicalClip(
        key=("catdv", str(clip_id)),
        name=f"Clip_{clip_id:04d}",
        duration_secs=60.0,
        fps=25.0,
        markers=(),
        fields={},
        notes={},
        media=MediaRef(
            mime_type="video/quicktime",
            size_bytes=None,
            cached_path=None,
            upstream_handle=str(clip_id),
        ),
        provider_data={"ID": clip_id, "name": f"Clip_{clip_id:04d}"},
        fetched_at=now,
    )


class _PaginatingFakeArchive:
    """Fake archive that honours offset/limit — models real CatDV pagination.

    Holds N clips in memory but returns only the requested page slice to the
    route.  The route therefore always receives at most `limit` clips per
    render, matching production behaviour.
    """

    id = "catdv"

    def __init__(self, clips: list[CanonicalClip]):
        self._clips = clips

    async def list_clips(self, catalog_id: str, query: ClipQuery) -> ClipPage:
        page = self._clips[query.offset : query.offset + query.limit]
        return ClipPage(
            items=page,
            total=len(self._clips),
            offset=query.offset,
            limit=query.limit,
        )

    async def get_clip(self, clip_id_str: str) -> CanonicalClip:
        for c in self._clips:
            if c.key[1] == clip_id_str:
                return c
        raise ProviderError(f"clip {clip_id_str} not found")


async def _seed_n_clips(ctx, n: int) -> None:
    """Seed N rows into clip_cache and a few review_items rows.

    The review_items path (list_pending_clips) is a single SQL query
    regardless of N — seeding a handful exercises that code path without
    changing the expected query count.
    """
    now = datetime.now(UTC).isoformat()

    for i in range(1, n + 1):
        await ctx.db.execute(
            "INSERT INTO clip_cache "
            "(provider_id, provider_clip_id, name, catalog_id, "
            "duration_secs, fps, canonical_json, provider_etag, fetched_at) "
            "VALUES (?, ?, ?, '881507', 60.0, 25.0, '{}', NULL, ?)",
            ("catdv", str(i), f"Clip_{i:04d}", now),
        )
    await ctx.db.commit()

    # Seed one annotation + one pending review_item to exercise
    # list_pending_clips (still a single query regardless of N).
    cur = await ctx.db.execute(
        "INSERT INTO annotations "
        "(catdv_clip_id, catdv_clip_name, prompt_version_id, job_id, model, "
        "prompt_used, raw_response, structured_output, clip_snapshot, created_at) "
        "VALUES (1, 'Clip_0001', 1, NULL, 'm', 'p', '{}', '{}', '{}', ?)",
        (now,),
    )
    ann_id = cur.lastrowid
    await ctx.db.execute(
        "INSERT INTO review_items "
        "(annotation_id, studio_run_id, catdv_clip_id, kind, "
        "target_identifier, proposed_value, edited_value, decision) "
        "VALUES (?, NULL, 1, 'marker', NULL, '{}', NULL, 'pending')",
        (ann_id,),
    )
    await ctx.db.commit()


def _count_render(monkeypatch, tmp_path, n: int) -> int:
    """Boot a fresh app, seed N clips, render GET /?limit=10, return query count."""
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.core_ctx

        # Seed DB while the TestClient lifespan is active (same as other tests).
        asyncio.run(_seed_n_clips(ctx, n))

        # Wire the paginating fake archive after lifespan so live_ctx is set.
        all_clips = [_canonical(i) for i in range(1, n + 1)]
        install_live_ctx(app, archive=_PaginatingFakeArchive(all_clips))

        db = ctx.db
        orig_execute = db.execute
        orig_executemany = db.executemany
        stmt_count = 0

        async def _count_execute(*args, **kwargs):
            nonlocal stmt_count
            stmt_count += 1
            return await orig_execute(*args, **kwargs)

        async def _count_executemany(*args, **kwargs):
            nonlocal stmt_count
            stmt_count += 1
            return await orig_executemany(*args, **kwargs)

        db.execute = _count_execute  # type: ignore[method-assign]
        db.executemany = _count_executemany  # type: ignore[method-assign]
        try:
            r = client.get("/?limit=10")
        finally:
            db.execute = orig_execute  # type: ignore[method-assign]
            db.executemany = orig_executemany  # type: ignore[method-assign]

        assert r.status_code == 200, r.text

    return stmt_count


# ---------------------------------------------------------------------------
# N+1 pin tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n_clips", [10, 100, 1000])
def test_clips_list_query_count_bounded(monkeypatch, tmp_path, n_clips):
    """GET / issues ≤ 10 SQL statements at every clip-count (N=10/100/1000).

    The page is limited to 10 rows (limit=10), well inside a single
    chunked_in_clause batch.  Actual count at the time of writing: 8.
    Bound is set to 10 (actual + 2 headroom) so a single per-row DB call
    (adds 10 statements for a 10-row page) trips the assertion.
    """
    count = _count_render(monkeypatch, tmp_path, n_clips)

    assert count <= 10, (
        f"[n={n_clips}] query count {count} > 10; "
        "an N+1 may have been introduced in the clips-list render. "
        "See ADR 0046."
    )


def test_clips_list_query_count_identical_across_n(monkeypatch, tmp_path):
    """Query count is identical at N=10, N=100, and N=1000.

    Equality (not just ≤ bound) is the sharpest possible pin: even one extra
    query that fires once at N=100 but not at N=10 will fail this test.

    Each N gets its own subdirectory so the DB files are isolated.
    """
    counts: dict[int, int] = {}
    for n in (10, 100, 1000):
        sub = tmp_path / f"n{n}"
        sub.mkdir()
        counts[n] = _count_render(monkeypatch, sub, n)

    assert counts[10] == counts[100] == counts[1000], (
        f"query counts differ across N: {counts}; "
        "the render is no longer O(1) in clip count. See ADR 0046."
    )
