import asyncio
import importlib

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


async def _seed(ctx):
    from backend.app.models.annotation import Annotation, ReviewItem

    _, vid = await ctx.prompts_repo.create_with_initial_version(
        ctx.db,
        name="t",
        description=None,
        body="p",
        target_map={
            "scenes": {"kind": "markers"},
            "decade": {"kind": "field", "identifier": "pragafilm.dekáda.natočení"},
        },
        output_schema={},
        model="m",
    )
    aid = await ctx.annotations_repo.insert(
        ctx.db,
        Annotation(
            catdv_clip_id=1,
            catdv_clip_name="Clip_1",
            prompt_version_id=vid,
            model="m",
            prompt_used="p",
            raw_response={},
            structured_output={},
            clip_snapshot={"ID": 1, "name": "Clip_1", "markers": [], "fields": {}},
        ),
    )
    items = await ctx.review_items_repo.bulk_insert(
        ctx.db,
        [
            ReviewItem(
                annotation_id=aid,
                catdv_clip_id=1,
                kind="marker",
                proposed_value={
                    "name": "scene-a",
                    "in": {"frm": 0, "secs": 0.0},
                    "out": {"frm": 25, "secs": 1.0},
                },
            ),
            ReviewItem(
                annotation_id=aid,
                catdv_clip_id=1,
                kind="field",
                target_identifier="pragafilm.dekáda.natočení",
                proposed_value="30.léta",
            ),
        ],
    )
    return vid, aid, items


def _make_app(monkeypatch, tmp_path):
    _setenv(monkeypatch, tmp_path)
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    return main_mod.app


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_list_pending_items(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.ctx
        _run(_seed(ctx))
        r = client.get("/api/review/clips/1/items")
        assert r.status_code == 200
        assert len(r.json()) == 2


def test_set_decision_accept(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.ctx
        _, _, items = _run(_seed(ctx))
        item_id = items[0].id

        r = client.post(f"/api/review/items/{item_id}/decision", json={"decision": "accepted"})
        assert r.status_code == 200
        r = client.get("/api/review/clips/1/items")
        accepted = [it for it in r.json() if it["decision"] == "accepted"]
        assert len(accepted) == 1


def test_apply_clip_enqueues_and_drains_via_sync_engine(monkeypatch, tmp_path):
    from backend.app.archive.model import ChangeSet, SetField, WriteResult
    from backend.app.repositories.pending_operations import PendingOperationsRepo
    from backend.app.repositories.write_log import WriteLogRepo
    from backend.app.services.connection_monitor import ConnectionState
    from backend.app.services.sync_engine import SyncEngine

    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.ctx
        _, _, items = _run(_seed(ctx))

        class FakeArchive:
            id = "catdv"
            last_change_set: ChangeSet | None = None

            async def apply_changes(self, change_set: ChangeSet) -> WriteResult:
                FakeArchive.last_change_set = change_set
                return WriteResult(
                    status="ok",
                    upstream_response={
                        "ID": int(change_set.clip_key[1]),
                        "modifyDate": "2026-05-18",
                    },
                )

        class AlwaysOnline:
            def current_state(self):
                return ConnectionState.online

        ctx.archive = FakeArchive()
        ctx.sync_engine = SyncEngine(
            provider=ctx.archive,
            pending_ops_repo=PendingOperationsRepo(),
            write_log_repo=WriteLogRepo(),
            connection_monitor=AlwaysOnline(),
            db_provider=lambda: ctx.db,
        )

        for it in items:
            client.post(
                f"/api/review/items/{it.id}/decision",
                json={"decision": "accepted"},
            )

        r = client.post("/api/review/clips/1/apply")
        assert r.status_code == 200
        body = r.json()
        assert body["queued"] >= 1

        # Drain explicitly (the route only notifies; the lifespan-managed
        # background loop is not started here because init_external=False).
        n = _run(ctx.sync_engine.drain_once())
        assert n == 1
        cs = FakeArchive.last_change_set
        assert cs is not None
        assert cs.clip_key == ("catdv", "1")
        op_types = {type(o).__name__ for o in cs.ops}
        assert "AddMarkers" in op_types
        assert any(
            isinstance(o, SetField)
            and o.identifier == "pragafilm.dekáda.natočení"
            and o.value == "30.léta"
            for o in cs.ops
        )


def test_pending_lists_clip(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.ctx
        _run(_seed(ctx))
        r = client.get("/api/review/pending")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        row = body["clips"][0]
        assert row["catdv_clip_id"] == 1
        assert row["marker_count"] == 1
        assert row["field_count"] == 1


def test_pending_count(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.ctx
        _run(_seed(ctx))
        r = client.get("/api/review/pending/count")
        assert r.status_code == 200
        assert r.json()["count"] == 1


def test_apply_batch_marks_and_enqueues_filtered_by_kind(monkeypatch, tmp_path):
    from backend.app.archive.model import ChangeSet, WriteResult
    from backend.app.repositories.pending_operations import PendingOperationsRepo
    from backend.app.repositories.write_log import WriteLogRepo
    from backend.app.services.connection_monitor import ConnectionState
    from backend.app.services.sync_engine import SyncEngine

    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.ctx
        _run(_seed(ctx))

        class FakeArchive:
            id = "catdv"
            async def apply_changes(self, change_set: ChangeSet) -> WriteResult:
                return WriteResult(status="ok", upstream_response={"ID": 1, "modifyDate": "x"})

        class AlwaysOnline:
            def current_state(self):
                return ConnectionState.online

        ctx.archive = FakeArchive()
        ctx.sync_engine = SyncEngine(
            provider=ctx.archive,
            pending_ops_repo=PendingOperationsRepo(),
            write_log_repo=WriteLogRepo(),
            connection_monitor=AlwaysOnline(),
            db_provider=lambda: ctx.db,
        )

        r = client.post("/api/review/apply-batch", json={"clip_ids": [1], "kinds": ["marker"]})
        assert r.status_code == 200
        assert r.json()["clips"] == 1
        assert r.json()["queued"] >= 1

        items = client.get("/api/review/clips/1/items").json()
        markers = [it for it in items if it["kind"] == "marker"]
        fields = [it for it in items if it["kind"] == "field"]
        assert all(it["applied_at"] for it in markers)
        assert all(it["applied_at"] is None for it in fields)


def test_apply_batch_defaults_all_kinds(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.ctx
        _run(_seed(ctx))
        r = client.post("/api/review/apply-batch", json={"clip_ids": [1]})
        assert r.status_code == 200
        assert r.json()["queued"] >= 2


def test_apply_batch_does_not_flush_preaccepted_other_kinds(monkeypatch, tmp_path):
    """Regression: apply-batch with kinds=["marker"] must NOT flush a field item
    that was already accepted (but not yet applied) before the batch call."""
    from backend.app.archive.model import ChangeSet, WriteResult
    from backend.app.repositories.pending_operations import PendingOperationsRepo
    from backend.app.repositories.write_log import WriteLogRepo
    from backend.app.services.connection_monitor import ConnectionState
    from backend.app.services.sync_engine import SyncEngine

    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.ctx
        _run(_seed(ctx))

        class FakeArchive:
            id = "catdv"

            async def apply_changes(self, change_set: ChangeSet) -> WriteResult:
                return WriteResult(status="ok", upstream_response={"ID": 1, "modifyDate": "x"})

        class AlwaysOnline:
            def current_state(self):
                return ConnectionState.online

        ctx.archive = FakeArchive()
        ctx.sync_engine = SyncEngine(
            provider=ctx.archive,
            pending_ops_repo=PendingOperationsRepo(),
            write_log_repo=WriteLogRepo(),
            connection_monitor=AlwaysOnline(),
            db_provider=lambda: ctx.db,
        )

        # Find the field item id via GET /api/review/clips/1/items
        items_resp = client.get("/api/review/clips/1/items")
        assert items_resp.status_code == 200
        all_items = items_resp.json()
        field_items = [it for it in all_items if it["kind"] == "field"]
        assert field_items, "seed must produce at least one field item"
        field_id = field_items[0]["id"]

        # Pre-accept the field item (simulating human-in-the-loop acceptance
        # before any bulk apply has run)
        r = client.post(f"/api/review/items/{field_id}/decision", json={"decision": "accepted"})
        assert r.status_code == 200

        # Now apply-batch with kinds=["marker"] only
        r = client.post("/api/review/apply-batch", json={"clip_ids": [1], "kinds": ["marker"]})
        assert r.status_code == 200
        assert r.json()["clips"] == 1

        # Drain the sync engine so any enqueued ops are applied upstream
        _run(ctx.sync_engine.drain_once())

        # Verify post-condition: marker applied, field NOT applied
        items_after = client.get("/api/review/clips/1/items").json()
        markers_after = [it for it in items_after if it["kind"] == "marker"]
        fields_after = [it for it in items_after if it["kind"] == "field"]

        assert all(it["applied_at"] is not None for it in markers_after), (
            "marker items must be applied after apply-batch markers"
        )
        assert all(it["applied_at"] is None for it in fields_after), (
            "field item was pre-accepted but must NOT be flushed by a marker-only apply-batch"
        )


def test_apply_batch_503_when_no_write_queue(monkeypatch, tmp_path):
    """apply-batch must return 503 when write_queue is not initialised."""
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.ctx
        _run(_seed(ctx))
        ctx.write_queue = None
        r = client.post("/api/review/apply-batch", json={"clip_ids": [1]})
        assert r.status_code == 503


def test_apply_batch_400_on_bad_kind(monkeypatch, tmp_path):
    """apply-batch must return 400 when an unrecognised kind is supplied."""
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.ctx
        _run(_seed(ctx))
        r = client.post("/api/review/apply-batch", json={"clip_ids": [1], "kinds": ["bogus"]})
        assert r.status_code == 400


def test_review_page_renders(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.ctx
        _run(_seed(ctx))
        r = client.get("/review")
        assert r.status_code == 200
        assert "Clip_1" in r.text
        assert "row-check" in r.text


def test_review_page_htmx_returns_table_only(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.ctx
        _run(_seed(ctx))
        r = client.get("/review", headers={"HX-Request": "true"})
        assert r.status_code == 200
        assert "<table" in r.text
        assert "<aside" not in r.text
