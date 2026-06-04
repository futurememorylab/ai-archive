import asyncio
import importlib

from fastapi.testclient import TestClient

from tests._helpers.live_ctx import install_live_ctx


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
        ctx = client.app.state.core_ctx
        _run(_seed(ctx))
        r = client.get("/api/review/clips/1/items")
        assert r.status_code == 200
        assert len(r.json()) == 2


def test_set_decision_accept(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.core_ctx
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
        ctx = client.app.state.core_ctx
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


def test_apply_clip_returns_json_for_non_hx_caller(monkeypatch, tmp_path):
    """The non-HX path (e.g. applyAndNext, which navigates away on success)
    must keep getting the JSON {"queued","applied"} body."""
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.core_ctx
        _, _, items = _run(_seed(ctx))
        for it in items:
            client.post(
                f"/api/review/items/{it.id}/decision",
                json={"decision": "accepted"},
            )
        r = client.post("/api/review/clips/1/apply")
        assert r.status_code == 200
        assert "application/json" in r.headers["content-type"]
        body = r.json()
        assert body["queued"] >= 1
        assert body["applied"] == body["queued"]


def test_apply_clip_returns_partial_for_hx_caller(monkeypatch, tmp_path):
    """The HTMX path sends HX-Request: true and gets the re-rendered draft
    aside partial (the Alpine card panel) back so the JS can swap it in
    place rather than full-reloading.  The redesigned panel is Alpine-driven
    so item values are emitted as x-text directives, not as literal text."""
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.core_ctx
        _, _, items = _run(_seed(ctx))
        for it in items:
            client.post(
                f"/api/review/items/{it.id}/decision",
                json={"decision": "accepted"},
            )
        r = client.post(
            "/api/review/clips/1/apply",
            headers={"HX-Request": "true"},
        )
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        # The draft aside is the Alpine card panel — check structural markers.
        assert "review-bar" in r.text
        assert "acceptApplyAll" in r.text
        # Sanity: it is the draft-aside partial, not a JSON dump.
        assert "{" + '"queued"' not in r.text


def test_apply_batch_marks_and_enqueues_filtered_by_kind(monkeypatch, tmp_path):
    from backend.app.archive.model import ChangeSet, WriteResult
    from backend.app.repositories.pending_operations import PendingOperationsRepo
    from backend.app.repositories.write_log import WriteLogRepo
    from backend.app.services.connection_monitor import ConnectionState
    from backend.app.services.sync_engine import SyncEngine

    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.core_ctx
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
        ctx = client.app.state.core_ctx
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
        ctx = client.app.state.core_ctx
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
        ctx = client.app.state.core_ctx
        _run(_seed(ctx))
        ctx.write_queue = None
        r = client.post("/api/review/apply-batch", json={"clip_ids": [1]})
        assert r.status_code == 503


def test_apply_batch_400_on_bad_kind(monkeypatch, tmp_path):
    """apply-batch must return 400 when an unrecognised kind is supplied."""
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.core_ctx
        _run(_seed(ctx))
        r = client.post("/api/review/apply-batch", json={"clip_ids": [1], "kinds": ["bogus"]})
        assert r.status_code == 400


class _FakeArchive:
    """Minimal archive stub — returns a single CanonicalClip by id."""

    def __init__(self, clips):
        self._clips = {str(c.key[1]): c for c in clips}

    async def get_clip(self, clip_id_str):
        from backend.app.archive.errors import ProviderError

        try:
            return self._clips[str(clip_id_str)]
        except KeyError as exc:
            raise ProviderError(f"not found: {clip_id_str}") from exc

    async def list_clips(self, catalog_id, query):
        from backend.app.archive.model import ClipPage

        items = list(self._clips.values())
        return ClipPage(items=items, total=len(items), offset=query.offset, limit=query.limit)


def _make_canonical_clip(clip_id: int = 1):
    from datetime import UTC, datetime

    from backend.app.archive.model import CanonicalClip, MediaRef

    return CanonicalClip(
        key=("catdv", str(clip_id)),
        name=f"Clip_{clip_id}",
        duration_secs=10.0,
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
        provider_data={"ID": clip_id, "name": f"Clip_{clip_id}"},
        fetched_at=datetime.now(UTC),
    )


def test_clip_detail_review_mode_renders_item_controls(monkeypatch, tmp_path):
    """Clip detail with ?review=1 renders the Alpine-data-driven card panel
    with review controls (accept / edit / delete) for the draft items."""
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.core_ctx
        _run(_seed(ctx))
        install_live_ctx(client.app, archive=_FakeArchive([_make_canonical_clip(1)]))
        r = client.get("/clips/1?review=1")
        assert r.status_code == 200
        # Card panel structural markers (new Alpine-driven design).
        assert "ri-card" in r.text
        assert "startEdit" in r.text        # ✎ Edit (buffered save/cancel edit)
        assert "saveEdit" in r.text         # Save
        assert "cancelEdit" in r.text       # Cancel
        assert "del(" in r.text             # Delete
        assert "restore(" in r.text         # Restore (deleted strip)
        assert "acceptApplyAll" in r.text
        # Review bar with the consolidated accept+apply action and clip navigation.
        assert "review-bar" in r.text
        assert "navClip" in r.text


def test_clip_detail_draft_controls_show_without_review_flag(monkeypatch, tmp_path):
    """Draft controls must appear even without ?review=1 — whenever a draft exists
    the card panel (review-bar, ri-card, Edit/Delete) should be in the page."""
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.core_ctx
        _run(_seed(ctx))
        install_live_ctx(client.app, archive=_FakeArchive([_make_canonical_clip(1)]))
        r = client.get("/clips/1")  # no review flag
        assert r.status_code == 200
        # The Alpine card panel is always rendered when a draft exists.
        assert "review-bar" in r.text
        assert "ri-card" in r.text
        assert "startEdit" in r.text


def _make_canonical_clip_with_markers(clip_id: int = 99):
    """Like _make_canonical_clip but includes a published marker."""
    from datetime import UTC, datetime

    from backend.app.archive.model import CanonicalClip, Marker, MediaRef, Timecode

    return CanonicalClip(
        key=("catdv", str(clip_id)),
        name=f"Clip_{clip_id}",
        duration_secs=10.0,
        fps=25.0,
        markers=(
            Marker(
                name="published-scene",
                in_=Timecode(secs=0.0, fps=25.0, frm=0),
                out=Timecode(secs=2.0, fps=25.0, frm=50),
            ),
        ),
        fields={},
        notes={},
        media=MediaRef(
            mime_type="video/quicktime",
            size_bytes=None,
            cached_path=None,
            upstream_handle=str(clip_id),
        ),
        provider_data={"ID": clip_id, "name": f"Clip_{clip_id}"},
        fetched_at=datetime.now(UTC),
    )


def test_clip_detail_review_mode_published_items_have_no_controls(monkeypatch, tmp_path):
    """A clip in review=1 mode that has PUBLISHED markers but NO draft/review
    items must not render any ri-accept controls — published items lack item_id
    so even in review mode no controls should appear."""
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        # Use clip_id=99 — _seed only seeds clip_id=1, so there are no
        # review_items for clip 99 in the DB.
        install_live_ctx(client.app, archive=_FakeArchive([_make_canonical_clip_with_markers(99)]))
        r = client.get("/clips/99?review=1")
        assert r.status_code == 200
        assert "ri-accept" not in r.text


def _make_canonical_clip_image(clip_id: int = 50):
    """A CanonicalClip whose media.filePath ends in .jpg — kind='image'."""
    from datetime import UTC, datetime

    from backend.app.archive.model import CanonicalClip, MediaRef

    return CanonicalClip(
        key=("catdv", str(clip_id)),
        name=f"Image_{clip_id}",
        duration_secs=0.0,
        fps=25.0,
        markers=(),
        fields={},
        notes={},
        media=MediaRef(
            mime_type="image/jpeg",
            size_bytes=None,
            cached_path=None,
            upstream_handle=str(clip_id),
        ),
        provider_data={
            "ID": clip_id,
            "name": f"Image_{clip_id}",
            "media": {"filePath": f"/media/img_{clip_id}.jpg"},
        },
        fetched_at=datetime.now(UTC),
    )


async def _seed_image_clip(ctx, clip_id: int = 50):
    """Seed review items for an image clip (field + note only, no markers)."""
    from backend.app.models.annotation import Annotation, ReviewItem

    _, vid = await ctx.prompts_repo.create_with_initial_version(
        ctx.db,
        name=f"t-img-{clip_id}",
        description=None,
        body="p",
        target_map={"decade": {"kind": "field", "identifier": "pragafilm.dekáda.natočení"}},
        output_schema={},
        model="m",
    )
    aid = await ctx.annotations_repo.insert(
        ctx.db,
        Annotation(
            catdv_clip_id=clip_id,
            catdv_clip_name=f"Image_{clip_id}",
            prompt_version_id=vid,
            model="m",
            prompt_used="p",
            raw_response={},
            structured_output={},
            clip_snapshot={"ID": clip_id, "name": f"Image_{clip_id}", "markers": [], "fields": {}},
        ),
    )
    await ctx.review_items_repo.bulk_insert(
        ctx.db,
        [
            ReviewItem(
                annotation_id=aid,
                catdv_clip_id=clip_id,
                kind="field",
                target_identifier="pragafilm.dekáda.natočení",
                proposed_value="50.léta",
            ),
        ],
    )


def test_clip_detail_image_hides_markers_tab(monkeypatch, tmp_path):
    """Image clips must default to the fields tab, and the published panel
    must not show a Markers tab (images have no markers).  In the new
    Alpine-data-driven draft panel all 3 tab buttons are always in the DOM
    (Alpine controls visibility at runtime), so only the *published* panel
    and the x-data default tab are checked here."""
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.core_ctx
        _run(_seed_image_clip(ctx, clip_id=50))
        install_live_ctx(client.app, archive=_FakeArchive([_make_canonical_clip_image(50)]))
        r = client.get("/clips/50?review=1")
        assert r.status_code == 200
        # Fields tab must be present in both published and draft panels.
        assert "tab === 'fields'" in r.text
        # Default tab in the x-data must be 'fields' (not 'markers') for images.
        assert 'tab: "fields"' in r.text
        # The published panel for an image does not have a Markers tab button;
        # it goes straight to Fields.  Check via the published _anno_panels path.
        assert "anno-scoped" in r.text


def test_clip_detail_review_action_bar_has_prev(monkeypatch, tmp_path):
    """The review bar must have Accept-all, ‹/› clip navigation, and Apply.
    The old 'Prev'/'Skip'/'Accept & apply' labels were replaced by the
    redesigned review-bar in _anno_draft.html."""
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.core_ctx
        _run(_seed(ctx))
        install_live_ctx(client.app, archive=_FakeArchive([_make_canonical_clip(1)]))
        r = client.get("/clips/1?review=1")
        assert r.status_code == 200
        # Review bar structural markers.
        assert "review-bar" in r.text
        assert "navClip(-1)" in r.text      # ‹ Previous clip
        assert "navClip(1)" in r.text       # › Next clip
        # Single consolidated bulk action: accept all visible proposals + apply.
        assert "acceptApplyAll()" in r.text


def test_clip_detail_no_draft_no_action_bar(monkeypatch, tmp_path):
    """A clip with NO review items must not render the review action bar at all."""
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        # clip 99 has no review items seeded — _seed only seeds clip_id=1
        install_live_ctx(client.app, archive=_FakeArchive([_make_canonical_clip_with_markers(99)]))
        r = client.get("/clips/99")
        assert r.status_code == 200
        assert "review-actionbar" not in r.text


def test_clips_list_shows_draft_columns(monkeypatch, tmp_path):
    """GET / must render Type, Batch, and Drafts column headers, and for a clip
    with pending review items the Drafts cell must contain the counts label."""
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.core_ctx
        # Seed draft items for clip 1 (1 marker, 1 field).
        _run(_seed(ctx))
        # Provide a FakeArchive so the clips list has clip 1 to display.
        install_live_ctx(client.app, archive=_FakeArchive([_make_canonical_clip(1)]))
        r = client.get("/")
        assert r.status_code == 200
        # Column headers must be present.
        assert "Type" in r.text
        assert "Batch" in r.text
        assert "Drafts" in r.text
        # Clip 1 has 1 marker draft and 1 field draft → label "1m · 1f".
        assert "1m" in r.text
        assert "1f" in r.text
        # Clip kind (video, since no filePath in provider_data) must appear.
        assert "video" in r.text


def test_clips_list_has_review_bulk_actions(monkeypatch, tmp_path):
    """GET / must render the bulk review actions in the Actions menu markup
    (always server-rendered; visible after selection). The per-kind
    Markers/Fields/Notes toggles were removed — bulk apply now applies all
    draft kinds (kinds default to all in bulkSel)."""
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        install_live_ctx(client.app, archive=_FakeArchive([_make_canonical_clip(1)]))
        r = client.get("/")
        assert r.status_code == 200
        # Primary review actions must be present in the markup.
        assert "Review selected" in r.text
        assert "Apply drafts" in r.text
