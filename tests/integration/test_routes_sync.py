"""Sync drawer routes: list pending, run drain, retry, discard."""

import importlib
import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.archive.change_set_json import change_op_to_json
from backend.app.archive.model import SetField, WriteResult
from backend.app.repositories.pending_operations import PendingOperationsRepo
from backend.app.repositories.write_log import WriteLogRepo
from backend.app.services.connection_monitor import ConnectionState
from backend.app.services.sync_engine import SyncEngine


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


def _enqueue_one(client, *, clip_id="1"):
    """Insert a single pending op directly via the repo."""
    ctx = client.app.state.core_ctx
    repo = ctx.pending_ops_repo
    op = SetField(identifier="x", value=1)
    rows = [
        {
            "provider_id": "catdv",
            "provider_clip_id": clip_id,
            "op_kind": "SetField",
            "op_json": change_op_to_json(op),
            "origin_annotation_id": None,
            "origin_review_item_ids": None,
            "expected_etag": None,
        }
    ]
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(repo.insert_many(ctx.db, rows=rows))
    finally:
        loop.close()


def test_get_pending_lists_rows(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        _enqueue_one(client, clip_id="42")
        r = client.get("/api/sync/pending")
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) == 1
        assert rows[0]["op_kind"] == "SetField"
        assert rows[0]["provider_clip_id"] == "42"


def test_post_discard_removes_row(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        [op_id] = _enqueue_one(client)
        r = client.post(f"/api/sync/pending/{op_id}/discard")
        assert r.status_code == 200
        r = client.get("/api/sync/pending")
        assert r.json() == []


def test_post_retry_resets_row(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        [op_id] = _enqueue_one(client)
        ctx = client.app.state.core_ctx
        # mark a couple of retries first
        import asyncio

        async def _bump():
            await ctx.pending_ops_repo.mark_retryable(ctx.db, [op_id], error="x")
            await ctx.pending_ops_repo.mark_retryable(ctx.db, [op_id], error="x")

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_bump())
        finally:
            loop.close()
        r = client.post(f"/api/sync/pending/{op_id}/retry")
        assert r.status_code == 200
        r = client.get("/api/sync/pending")
        assert r.json()[0]["attempts"] == 0
        assert r.json()[0]["last_error"] is None


def test_run_drain_invokes_engine(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        _enqueue_one(client)
        from tests._helpers.live_ctx import install_live_ctx

        ctx = client.app.state.core_ctx

        class FakeArchive:
            id = "catdv"

            async def apply_changes(self, change_set):
                return WriteResult(
                    status="ok",
                    upstream_response={"ID": 1, "modifyDate": "x"},
                )

        class AlwaysOnline:
            def current_state(self):
                return ConnectionState.online

        archive = FakeArchive()
        install_live_ctx(
            client.app,
            archive=archive,
            sync_engine=SyncEngine(
                provider=archive,
                pending_ops_repo=PendingOperationsRepo(),
                write_log_repo=WriteLogRepo(),
                connection_monitor=AlwaysOnline(),
                db_provider=lambda: ctx.db,
            ),
        )
        r = client.post("/api/sync/run")
        assert r.status_code == 200
        assert r.json()["processed"] == 1
        r = client.get("/api/sync/pending")
        # row should now be applied (terminal status) — list_with_clip_names
        # only returns non-applied; expect empty
        assert r.json() == []


def test_clip_status_reports_counts(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ids = _enqueue_one(client, clip_id="55")
        _enqueue_one(client, clip_id="55")
        ctx = client.app.state.core_ctx
        import asyncio

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(ctx.pending_ops_repo.mark_failed(ctx.db, ids, error="x"))
        finally:
            loop.close()
        r = client.get("/api/sync/clip/55/status")
        assert r.status_code == 200
        body = r.json()
        assert body["clip_id"] == 55
        assert body["failed"] == 1
        assert body["pending"] == 1
        # unfinished = pending + in_flight; problems = failed + conflict
        assert body["unfinished"] == 1
        assert body["problems"] == 1
        assert body["done"] is False


def test_clip_status_done_when_all_applied(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ids = _enqueue_one(client, clip_id="56")
        ctx = client.app.state.core_ctx
        import asyncio

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(ctx.pending_ops_repo.mark_applied(ctx.db, ids))
        finally:
            loop.close()
        r = client.get("/api/sync/clip/56/status")
        assert r.status_code == 200
        body = r.json()
        assert body["applied"] == 1
        assert body["unfinished"] == 0
        assert body["problems"] == 0
        assert body["done"] is True


def test_retry_returns_chip_partial_on_hx_request(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        [op_id] = _enqueue_one(client)
        # HX request → the refreshed sync-chip partial (so the panel updates).
        r = client.post(f"/api/sync/pending/{op_id}/retry", headers={"HX-Request": "true"})
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "/ui/sync-chip" in r.text  # the chip inner (heartbeat poller)
        # Plain request → JSON, unchanged for API/tests.
        r2 = client.post(f"/api/sync/pending/{op_id}/retry")
        assert r2.headers["content-type"].startswith("application/json")
        assert r2.json()["reset"] is True


def test_discard_returns_chip_partial_on_hx_request(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        [op_id] = _enqueue_one(client)
        r = client.post(f"/api/sync/pending/{op_id}/discard", headers={"HX-Request": "true"})
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]


def test_retry_all_resets_failed_and_conflict(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        failed = _enqueue_one(client, clip_id="70")
        _enqueue_one(client, clip_id="71")  # stays pending, untouched
        ctx = client.app.state.core_ctx
        import asyncio

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(ctx.pending_ops_repo.mark_failed(ctx.db, failed, error="boom"))
        finally:
            loop.close()

        assert client.get("/api/sync/clip/70/status").json()["failed"] == 1
        r = client.post("/api/sync/retry-all")
        assert r.status_code == 200
        assert r.json()["reset"] == 1  # only the failed row was reset
        # failed → pending, and the already-pending row is left as-is
        assert client.get("/api/sync/clip/70/status").json()["pending"] == 1
        assert client.get("/api/sync/clip/70/status").json()["failed"] == 0
        assert client.get("/api/sync/clip/71/status").json()["pending"] == 1


def test_retry_clip_resets_only_that_clips_problems(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        a = _enqueue_one(client, clip_id="80")
        b = _enqueue_one(client, clip_id="81")
        ctx = client.app.state.core_ctx
        import asyncio

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(ctx.pending_ops_repo.mark_failed(ctx.db, a + b, error="boom"))
        finally:
            loop.close()

        r = client.post("/api/sync/clip/catdv/80/retry")
        assert r.status_code == 200
        assert r.json()["reset"] == 1
        # clip 80 reset to pending; clip 81 still failed (untouched)
        assert client.get("/api/sync/clip/80/status").json()["pending"] == 1
        assert client.get("/api/sync/clip/80/status").json()["failed"] == 0
        assert client.get("/api/sync/clip/81/status").json()["failed"] == 1


def test_discard_clip_removes_all_its_pending(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        _enqueue_one(client, clip_id="82")
        b = _enqueue_one(client, clip_id="82")
        ctx = client.app.state.core_ctx
        import asyncio

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(ctx.pending_ops_repo.mark_failed(ctx.db, b, error="boom"))
        finally:
            loop.close()

        r = client.post("/api/sync/clip/catdv/82/discard")
        assert r.status_code == 200
        assert r.json()["discarded"] == 2  # both the pending and the failed op
        body = client.get("/api/sync/clip/82/status").json()
        assert body["pending"] == 0
        assert body["failed"] == 0


def test_retry_404_when_missing(monkeypatch, tmp_path: Path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        r = client.post("/api/sync/pending/999999/retry")
        assert r.status_code == 404


# guard against import-unused warning
_ = json
