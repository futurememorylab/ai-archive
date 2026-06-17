"""UI partial routes — sanity-check that Jinja templates render."""

import importlib
from pathlib import Path

from fastapi.testclient import TestClient


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


def test_connection_pill_renders(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        r = client.get("/ui/connection-pill")
    assert r.status_code == 200
    assert "connection-pill" in r.text
    assert "Sync now" in r.text


def test_workspace_switcher_renders(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        r = client.get("/ui/workspace-switcher")
    assert r.status_code == 200
    assert "workspace-switcher" in r.text
    assert "All clips" in r.text


def test_sync_drawer_renders_empty(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        r = client.get("/ui/sync-drawer")
    assert r.status_code == 200
    assert "sync-drawer" in r.text
    assert "No pending writes" in r.text


def test_sync_drawer_humanises_status_and_drops_dead_conflict_button(monkeypatch, tmp_path: Path):
    import asyncio

    from backend.app.archive.change_set_json import change_op_to_json
    from backend.app.archive.model import AppendNote

    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.core_ctx
        repo = ctx.pending_ops_repo

        async def _seed():
            ids = await repo.insert_many(
                ctx.db,
                rows=[
                    {
                        "provider_id": "catdv",
                        "provider_clip_id": "7",
                        "op_kind": "AppendNote",
                        "op_json": change_op_to_json(AppendNote(target="notes", text="x")),
                        "origin_annotation_id": None,
                        "origin_review_item_ids": None,
                        "expected_etag": None,
                    },
                    {
                        "provider_id": "catdv",
                        "provider_clip_id": "8",
                        "op_kind": "SetField",
                        "op_json": '{"kind": "SetField", "identifier": "x", "value": 1}',
                        "origin_annotation_id": None,
                        "origin_review_item_ids": None,
                        "expected_etag": None,
                    },
                ],
            )
            await repo.mark_conflict(ctx.db, [ids[0]], conflict_detail={"kind": "modified"})
            await repo.mark_failed(ctx.db, [ids[1]], error="boom")

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_seed())
        finally:
            loop.close()

        r = client.get("/ui/sync-drawer")

    assert r.status_code == 200
    # Humanised, user-facing labels — not the raw enum / class names.
    assert "Conflict" in r.text
    assert "Failed" in r.text
    assert "Note" in r.text and "AppendNote" not in r.text  # op kind humanised
    assert "pill" in r.text  # reuses ui.status_pill
    assert "Changed in CatDV" in r.text  # conflict explained
    # The old dead button (no JS handler) must be gone.
    assert "view-conflict" not in r.text
    assert "View conflict" not in r.text
    # Bulk action present when there are failed/conflict rows.
    assert "Retry all" in r.text
    assert "/api/sync/retry-all" in r.text


def test_sync_drawer_groups_ops_by_clip(monkeypatch, tmp_path: Path):
    import asyncio

    from backend.app.archive.change_set_json import change_op_to_json
    from backend.app.archive.model import AddMarkers, AppendNote, Marker, Timecode

    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.core_ctx
        repo = ctx.pending_ops_repo

        async def _seed():
            ids = await repo.insert_many(
                ctx.db,
                rows=[
                    {
                        "provider_id": "catdv",
                        "provider_clip_id": "5",
                        "op_kind": "AppendNote",
                        "op_json": change_op_to_json(AppendNote(target="notes", text="x")),
                        "origin_annotation_id": None,
                        "origin_review_item_ids": None,
                        "expected_etag": None,
                    },
                    {
                        "provider_id": "catdv",
                        "provider_clip_id": "5",
                        "op_kind": "AddMarkers",
                        "op_json": change_op_to_json(
                            AddMarkers(
                                markers=(
                                    Marker(name="m", in_=Timecode(secs=1.0, fps=25.0), out=None),
                                )
                            )
                        ),
                        "origin_annotation_id": None,
                        "origin_review_item_ids": None,
                        "expected_etag": None,
                    },
                ],
            )
            await repo.mark_failed(ctx.db, [ids[0]], error="boom")  # worst = failed

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_seed())
        finally:
            loop.close()

        r = client.get("/ui/sync-drawer")

    assert r.status_code == 200
    # One clip, two ops → a single row combining both change kinds, worst status.
    assert "Note, Markers" in r.text
    assert "Failed" in r.text
    # Per-clip actions (not per-op).
    assert "/api/sync/clip/catdv/5/retry" in r.text
    assert "/api/sync/clip/catdv/5/discard" in r.text


def test_sync_chip_always_visible_when_empty(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        r = client.get("/ui/sync-chip")
    assert r.status_code == 200
    assert "sync-chip-trigger" in r.text  # always visible (queue status)
    assert "sync-chip-ok" in r.text  # idle "Synced" state
    assert "has-problems" not in r.text  # nothing wrong
    # Idle (nothing queued/problematic) → NO background poll. A perpetual 10s
    # heartbeat bought nothing and spammed the log; it re-arms only when there's
    # something to track (see test_sync_chip_shows_queued_and_problem_counts).
    assert 'hx-trigger="every 10s"' not in r.text


def test_sync_chip_shows_queued_and_problem_counts(monkeypatch, tmp_path: Path):
    import asyncio

    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.core_ctx
        repo = ctx.pending_ops_repo

        async def _seed():
            ids = await repo.insert_many(
                ctx.db,
                rows=[
                    {
                        "provider_id": "catdv",
                        "provider_clip_id": "1",
                        "op_kind": "SetField",
                        "op_json": '{"kind": "SetField", "identifier": "x", "value": 1}',
                        "origin_annotation_id": None,
                        "origin_review_item_ids": None,
                        "expected_etag": None,
                    },
                    {
                        "provider_id": "catdv",
                        "provider_clip_id": "2",
                        "op_kind": "SetField",
                        "op_json": '{"kind": "SetField", "identifier": "y", "value": 2}',
                        "origin_annotation_id": None,
                        "origin_review_item_ids": None,
                        "expected_etag": None,
                    },
                ],
            )
            await repo.mark_failed(ctx.db, [ids[1]], error="boom")  # 1 queued, 1 problem

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_seed())
        finally:
            loop.close()

        r = client.get("/ui/sync-chip")

    assert r.status_code == 200
    assert "sync-chip-trigger" in r.text
    assert "has-problems" in r.text  # a failed op present
    assert "sync-chip-queued" in r.text  # queued count rendered
    assert "sync-chip-problems" in r.text  # problem count rendered
    assert "Pending writes" in r.text  # panel reuses the drawer
    assert 'hx-trigger="every 10s"' in r.text  # poll armed while work is tracked


def test_clip_badge_renders_zero(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        r = client.get("/ui/clip-badge/catdv/1")
    assert r.status_code == 200
    # zero pending → no badge spans rendered
    assert "clip-badge" in r.text


def test_pages_have_breadcrumb_and_single_title(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        for path in ("/prompts", "/cache"):
            r = client.get(path)
            assert r.status_code == 200
            assert 'class="crumb"' in r.text  # top-bar context present
        cache = client.get("/cache").text
    # title not duplicated: "Cache" lives in the crumb leaf, not a body <h1>
    assert "<h1>Cache</h1>" not in cache
    assert '<span class="strong">Cache</span>' in cache


def test_cache_metric_cap_labelled_and_no_raw_bytes(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        r = client.get("/cache")
    assert r.status_code == 200
    # #4: the local-cache cap value is labelled as a cap (renders "of 50.0 GB cap").
    assert "GB cap" in r.text
    # #5: the AI-store m-foot no longer prints the redundant raw-byte line.
    # That line rendered as "<b>N</b> objects ·\n  <span class="muted-2">N B</span>",
    # so the "objects ·" separator and the raw-byte span are both gone.
    assert "objects ·" not in r.text
    assert 'class="muted-2">0 B' not in r.text
