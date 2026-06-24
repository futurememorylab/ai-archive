"""Topbar counts are computed by the `_load_topbar_counts` page-router dependency
(async, on the pooled connection) and stashed on `request.state`; the Jinja
context processor reads them with zero I/O of its own. Review finding #10."""

from types import SimpleNamespace

import pytest

from backend.app.context import CoreCtx
from backend.app.main import _load_topbar_counts
from backend.app.routes.pages.templates import _topbar_sync_context
from backend.app.settings import load_settings


def _req(*, core=None, live=None, headers=None) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(core_ctx=core, live_ctx=live)),
        state=SimpleNamespace(),
        headers=headers or {},
    )


@pytest.mark.asyncio
async def test_dependency_loads_counts_onto_request_state(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    ctx = await CoreCtx.build(load_settings())
    try:
        req = _req(core=ctx)
        await _load_topbar_counts(req)
        assert req.state.topbar_counts["sync_counts"] == {"queued": 0, "problems": 0}
        assert req.state.topbar_counts["review_count"] == 0
        # The always-present spend pill rides on topbar_counts too: spend_usd is
        # 0.0 (no telemetry seeded) and there's no budget set → status 'none'.
        assert req.state.topbar_counts["usage"] == {
            "spend_usd": 0.0,
            "budget_usd": None,
            "fraction": None,
            "status": "none",
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
        await _load_topbar_counts(req)
        assert req.state.topbar_counts["sync_counts"]["queued"] == 1
    finally:
        await ctx.aclose()


@pytest.mark.asyncio
async def test_dependency_skips_htmx_fragments(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    ctx = await CoreCtx.build(load_settings())
    try:
        req = _req(core=ctx, headers={"hx-request": "true"})
        await _load_topbar_counts(req)
        assert not hasattr(req.state, "topbar_counts")  # fragments don't draw the topbar
    finally:
        await ctx.aclose()


def test_context_processor_reads_request_state():
    """The processor returns the request-scoped counts with zero I/O of its own."""
    req = _req()
    usage = {"spend_usd": 1.5, "budget_usd": None, "fraction": None, "status": "none"}
    req.state.topbar_counts = {
        "sync_counts": {"queued": 3, "problems": 1},
        "review_count": 2,
        "usage": usage,
    }
    out = _topbar_sync_context(req)
    assert out == {
        "sync_counts": {"queued": 3, "problems": 1},
        "offline": False,
        "review_count": 2,
        "usage": usage,
    }


def test_context_processor_returns_empty_when_state_unpopulated():
    assert _topbar_sync_context(_req()) == {}
