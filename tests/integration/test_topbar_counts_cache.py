"""Topbar counts are cached in memory (CoreCtx.refresh_topbar_counts) and the
Jinja context processor reads that cache — no per-render sync sqlite connection.
Review finding #10."""

import pytest

from backend.app.context import CoreCtx
from backend.app.routes.pages.templates import _topbar_sync_context
from backend.app.settings import load_settings


@pytest.mark.asyncio
async def test_build_populates_topbar_counts_and_refresh_recomputes(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    ctx = await CoreCtx.build(load_settings())
    try:
        # build() runs an initial refresh, so the cache is populated (empty DB).
        assert ctx.topbar_counts == {
            "sync_counts": {"queued": 0, "problems": 0},
            "review_count": 0,
        }

        await ctx.pending_ops_repo.insert_many(
            ctx.db,
            rows=[
                {
                    "provider_id": "catdv",
                    "provider_clip_id": "1",
                    "op_kind": "SetField",
                    "op_json": "{}",
                    "origin_annotation_id": None,
                    "origin_review_item_ids": None,
                    "expected_etag": None,
                    "origin_clip_version_id": None,
                }
            ],
        )
        await ctx.refresh_topbar_counts()
        assert ctx.topbar_counts["sync_counts"]["queued"] == 1
    finally:
        await ctx.aclose()


def test_context_processor_reads_cache_not_sqlite():
    """The processor returns the cached counts with zero I/O; on a full-page
    render it must not open a database connection."""

    class _Core:
        topbar_counts = {"sync_counts": {"queued": 3, "problems": 1}, "review_count": 2}

    class _State:
        core_ctx = _Core()
        live_ctx = None

    class _Req:
        app = type("A", (), {"state": _State()})()
        headers: dict[str, str] = {}

    out = _topbar_sync_context(_Req())
    assert out == {"sync_counts": {"queued": 3, "problems": 1}, "offline": False, "review_count": 2}


def test_context_processor_returns_empty_when_cache_unpopulated():
    class _Core:
        topbar_counts = None

    class _Req:
        app = type("A", (), {"state": type("S", (), {"core_ctx": _Core(), "live_ctx": None})()})()
        headers: dict[str, str] = {}

    assert _topbar_sync_context(_Req()) == {}
