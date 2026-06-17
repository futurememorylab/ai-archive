"""Clips-list unified publish-status badge — render + N+1 guard.

Covers:
1. A clip with a live clip_version shows 'Live v<N>' in the rendered row.
2. A clip with only a pending draft (review_items, no version) shows 'Draft'.
3. A clip with no version and no draft shows no badge (no pill).
4. The batched status derivation is O(1) in clip count: the same number of
   SQL statements fires for 3 clips vs 30 clips (assert_query_count +
   identity across Ns).

Test harness follows the pattern from test_clips_page_perf.py.
"""

from __future__ import annotations

import asyncio
import importlib
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from backend.app.archive.model import (
    CanonicalClip,
    ClipPage,
    ClipQuery,
    MediaRef,
)
from tests._helpers.live_ctx import install_live_ctx

# ---------------------------------------------------------------------------
# Env + app factory (mirrors test_clips_page_perf.py)
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
# Helpers: canonical clip + paginating fake archive
# ---------------------------------------------------------------------------


def _canonical(clip_id: int) -> CanonicalClip:
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


class _FakeArchive:
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
        from backend.app.archive.errors import ProviderError

        raise ProviderError(f"clip {clip_id_str} not found")


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _seed_live_version(ctx, clip_id: int, version_num: int = 1) -> None:
    """Insert a live clip_version for `clip_id`."""
    now = datetime.now(UTC).isoformat()
    await ctx.db.execute(
        """
        INSERT INTO clip_versions
          (provider_id, catdv_clip_id, version_num, parent_version_id,
           snapshot, diff, origin, model, prompt_version_id, annotation_id,
           author, publish_state, expected_etag, failed_reason, synced_at)
        VALUES ('catdv', ?, ?, NULL, '{}', NULL, 'publish', NULL, NULL, NULL,
                NULL, 'live', NULL, NULL, ?)
        """,
        (clip_id, version_num, now),
    )
    await ctx.db.commit()


async def _seed_pending_draft(ctx, clip_id: int) -> None:
    """Insert an annotation + pending review_item so list_pending_clips sees it."""
    now = datetime.now(UTC).isoformat()
    cur = await ctx.db.execute(
        "INSERT INTO annotations "
        "(catdv_clip_id, catdv_clip_name, prompt_version_id, job_id, model, "
        "prompt_used, raw_response, structured_output, clip_snapshot, created_at) "
        "VALUES (?, ?, 1, NULL, 'm', 'p', '{}', '{}', '{}', ?)",
        (clip_id, f"Clip_{clip_id:04d}", now),
    )
    ann_id = cur.lastrowid
    await ctx.db.execute(
        "INSERT INTO review_items "
        "(annotation_id, studio_run_id, catdv_clip_id, kind, "
        "target_identifier, proposed_value, edited_value, decision) "
        "VALUES (?, NULL, ?, 'marker', NULL, '{}', NULL, 'pending')",
        (ann_id, clip_id),
    )
    await ctx.db.commit()


async def _seed_pending_op(ctx, clip_id: int, status: str) -> None:
    """Insert one pending_operations row for the clip in the given status —
    the write-queue signal that drives the publish headline."""
    now = datetime.now(UTC).isoformat()
    await ctx.db.execute(
        "INSERT INTO pending_operations "
        "(provider_id, provider_clip_id, op_kind, op_json, origin_annotation_id, "
        "origin_review_item_ids, expected_etag, origin_clip_version_id, status, "
        "attempts, enqueued_at) "
        "VALUES ('catdv', ?, 'SetField', '{}', NULL, NULL, NULL, NULL, ?, 0, ?)",
        (str(clip_id), status, now),
    )
    await ctx.db.commit()


# ---------------------------------------------------------------------------
# Badge render tests
# ---------------------------------------------------------------------------


def test_live_badge_rendered(monkeypatch, tmp_path):
    """A clip with a live version shows 'Live v1' status pill."""
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.core_ctx

        clip_id = 101
        asyncio.run(_seed_live_version(ctx, clip_id, version_num=1))

        install_live_ctx(app, archive=_FakeArchive([_canonical(clip_id)]))
        r = client.get("/")
        assert r.status_code == 200
        # The pill label for a live clip is "Live v1"
        assert "Live v1" in r.text, f"Expected 'Live v1' in response; got:\n{r.text[:3000]}"


def test_live_badge_version_num(monkeypatch, tmp_path):
    """The version number in the badge matches the newest version_num."""
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.core_ctx

        clip_id = 102
        asyncio.run(_seed_live_version(ctx, clip_id, version_num=3))

        install_live_ctx(app, archive=_FakeArchive([_canonical(clip_id)]))
        r = client.get("/")
        assert r.status_code == 200
        assert "Live v3" in r.text


def test_draft_badge_rendered(monkeypatch, tmp_path):
    """A clip with only a pending draft (no version) shows 'Draft' pill."""
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.core_ctx

        clip_id = 201
        asyncio.run(_seed_pending_draft(ctx, clip_id))

        install_live_ctx(app, archive=_FakeArchive([_canonical(clip_id)]))
        r = client.get("/")
        assert r.status_code == 200
        assert "Draft" in r.text, f"Expected 'Draft' in response; got:\n{r.text[:3000]}"


def test_failed_write_shows_failed_not_stale_version_state(monkeypatch, tmp_path):
    """A clip whose write FAILED shows 'Failed' even if its clip_version row is
    stale (stuck 'publishing'). The headline is sourced from pending_operations,
    so a drifted clip_versions copy can't make the badge lie. Publishing audit
    — the recurring stuck-'Publishing…' bug, fixed at the root."""
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.core_ctx
        clip_id = 401
        # Deliberately leave a stale 'publishing'-ish version AND a failed write:
        asyncio.run(_seed_live_version(ctx, clip_id, version_num=1))
        asyncio.run(_seed_pending_op(ctx, clip_id, "failed"))

        install_live_ctx(app, archive=_FakeArchive([_canonical(clip_id)]))
        r = client.get("/")
        assert r.status_code == 200
        assert "Failed" in r.text
        assert "Publishing" not in r.text


def test_pending_write_shows_publishing(monkeypatch, tmp_path):
    """A clip with an in-flight write shows 'Publishing…' from the queue."""
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.core_ctx
        clip_id = 402
        asyncio.run(_seed_pending_op(ctx, clip_id, "pending"))

        install_live_ctx(app, archive=_FakeArchive([_canonical(clip_id)]))
        r = client.get("/")
        assert r.status_code == 200
        assert "Publishing" in r.text


def test_no_badge_for_clean_clip(monkeypatch, tmp_path):
    """A clip with no version and no draft shows no publish-status pill."""
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        clip_id = 301
        install_live_ctx(app, archive=_FakeArchive([_canonical(clip_id)]))
        r = client.get("/")
        assert r.status_code == 200
        # No Live vN pill and no Draft pill for a clean clip.
        # "Draft" appears in other page chrome (column header "Drafts", JS "applyDrafts"),
        # so we check for the pill markup specifically.
        assert "Live v" not in r.text
        assert ">Draft<" not in r.text  # pill label, not surrounding chrome


# ---------------------------------------------------------------------------
# N+1 guard: statement count is CONSTANT across clip counts
# ---------------------------------------------------------------------------


async def _seed_clips_with_versions(ctx, clip_ids: list[int]) -> None:
    """Seed clip_cache rows + live versions for the given ids."""
    now = datetime.now(UTC).isoformat()
    for cid in clip_ids:
        await ctx.db.execute(
            "INSERT INTO clip_cache "
            "(provider_id, provider_clip_id, name, catalog_id, "
            "duration_secs, fps, canonical_json, provider_etag, fetched_at) "
            "VALUES (?, ?, ?, '881507', 60.0, 25.0, '{}', NULL, ?)",
            ("catdv", str(cid), f"Clip_{cid:04d}", now),
        )
    await ctx.db.commit()
    # Give half the clips a live version to exercise the batched path.
    for cid in clip_ids[: len(clip_ids) // 2]:
        await _seed_live_version(ctx, cid)


def _count_render(monkeypatch, tmp_path, n: int) -> int:
    """Boot a fresh app, seed N clips, render GET /?limit=10, return query count."""
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.core_ctx
        all_clip_ids = list(range(1, n + 1))
        asyncio.run(_seed_clips_with_versions(ctx, all_clip_ids))

        all_clips = [_canonical(i) for i in all_clip_ids]
        install_live_ctx(app, archive=_FakeArchive(all_clips))

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


@pytest.mark.parametrize("n_clips", [10, 100, 1000])
def test_clips_status_badge_query_count_bounded(monkeypatch, tmp_path, n_clips):
    """GET / with status-badge derivation issues ≤ 13 SQL statements at all clip counts.

    The badge now uses two batched reads — live_version_num_by_clip and
    count_pending_by_clip — instead of one newest_state_by_clip, both O(1) in
    clip count. Bound is 13 (actual + headroom) so a per-row DB lookup (10 extra
    statements for a 10-row page) trips the assertion.
    """
    count = _count_render(monkeypatch, tmp_path, n_clips)
    assert count <= 13, (
        f"[n={n_clips}] query count {count} > 13; "
        "an N+1 may have been introduced in the status-badge derivation. "
        "See ADR 0046."
    )


def test_clips_status_badge_query_count_identical_across_n(monkeypatch, tmp_path):
    """Statement count is identical at N=10, 100, and 1000 (O(1) in clip count).

    Each N gets its own subdirectory to isolate DB files.
    """
    counts: dict[int, int] = {}
    for n in (10, 100, 1000):
        sub = tmp_path / f"n{n}"
        sub.mkdir()
        counts[n] = _count_render(monkeypatch, sub, n)

    assert counts[10] == counts[100] == counts[1000], (
        f"query counts differ across N: {counts}; "
        "the status-badge render is not O(1) in clip count. See ADR 0046."
    )
