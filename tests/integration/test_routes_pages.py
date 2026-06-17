import dataclasses
import importlib
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from backend.app.archive.errors import ProviderError
from backend.app.archive.model import (
    CanonicalClip,
    ClipPage,
    FieldValue,
    Marker,
    MediaRef,
    Timecode,
)
from tests._helpers.live_ctx import install_live_ctx


def _canonical(clip_id: int = 12041, name: str = "Abramcukova_Anna_09") -> CanonicalClip:
    return CanonicalClip(
        key=("catdv", str(clip_id)),
        name=name,
        duration_secs=522.0,
        fps=25.0,
        markers=(
            Marker(
                name="Anna na zahradě",
                in_=Timecode(secs=83.48, fps=25.0),
                out=Timecode(secs=105.12, fps=25.0),
                description="Detailní záběr",
            ),
        ),
        fields={
            "pragafilm.dekáda.natočení": FieldValue(
                identifier="pragafilm.dekáda.natočení", value="30.léta"
            ),
            "pragafilm.rok.natočení": FieldValue(
                identifier="pragafilm.rok.natočení", value=["1932"], is_multi=True
            ),
        },
        notes={},
        media=MediaRef(
            mime_type="video/quicktime",
            size_bytes=None,
            cached_path=None,
            upstream_handle=str(clip_id),
        ),
        provider_data={
            "ID": clip_id,
            "name": name,
            "notes": "krátká poznámka",
        },
        fetched_at=datetime.now(UTC),
    )


def _make_client(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    return TestClient(main_mod.app)


class FakeArchive:
    def __init__(self, clips: tuple[CanonicalClip, ...] = (), total: int | None = None):
        self._clips = clips
        self._total = total
        self.last_query = None

    async def list_clips(self, catalog, query):
        self.last_query = query
        return ClipPage(
            items=self._clips,
            total=self._total if self._total is not None else len(self._clips),
            offset=query.offset,
            limit=query.limit,
        )

    async def get_clip(self, clip_id_str):
        for c in self._clips:
            if c.key[1] == clip_id_str:
                return c
        raise ProviderError("not found")


def test_clips_list_returns_full_page(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        install_live_ctx(client.app, archive=FakeArchive((_canonical(),)))
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "<!doctype html>" in r.text.lower()
        assert "Abramcukova_Anna_09" in r.text
        assert "1932" in r.text
        assert "30.léta" in r.text
        assert 'class="vlist"' in r.text
        assert "/api/media/12041/thumb" in r.text  # thumbnail img wired (clip id from _canonical)


def test_clips_actions_menu_uses_popover_module(monkeypatch, tmp_path):
    """The bulk Actions dropdown is migrated onto the shared popover/menu
    module — canonical .popover-panel/.menu/.menu-item, not the retired
    bespoke .actions-menu/.actions-item vocabulary."""
    with _make_client(monkeypatch, tmp_path) as client:
        install_live_ctx(client.app, archive=FakeArchive((_canonical(),)))
        r = client.get("/")
        assert r.status_code == 200
        # Migrated onto the shared module.
        assert 'class="popover actions-dropdown"' in r.text
        assert 'x-data="popover()"' in r.text
        assert 'class="popover-panel menu align-right"' in r.text
        assert 'class="menu-item"' in r.text
        # Every action preserved.
        assert "reviewSelected()" in r.text
        assert "applyDrafts()" in r.text
        assert "openAnnotate()" in r.text
        assert "bulkEvict()" in r.text
        # Bespoke vocabulary fully retired.
        assert "actions-menu" not in r.text
        assert 'class="actions-item' not in r.text
        assert "actions-sep" not in r.text


def test_clips_list_htmx_returns_partial(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        install_live_ctx(client.app, archive=FakeArchive((_canonical(),)))
        r = client.get("/", headers={"HX-Request": "true"})
        assert r.status_code == 200
        assert "<!doctype html>" not in r.text.lower()
        assert "Abramcukova_Anna_09" in r.text


def test_clips_list_passes_query_to_adapter(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        fake = FakeArchive((_canonical(),))
        install_live_ctx(client.app, archive=fake)
        r = client.get("/?q=Anna&offset=20&limit=10")
        assert r.status_code == 200
        assert fake.last_query.text == "Anna"
        assert fake.last_query.offset == 20
        assert fake.last_query.limit == 10


def test_clip_detail_renders(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        install_live_ctx(client.app, archive=FakeArchive((_canonical(),)))
        r = client.get("/clips/12041")
        assert r.status_code == 200
        assert "Abramcukova_Anna_09" in r.text
        assert "Anna na zahradě" in r.text
        assert "/api/media/12041" in r.text


def test_clip_detail_shows_annotation_cost(monkeypatch, tmp_path):
    """The published-annotation panel shows the originating run's actual cost."""
    import asyncio

    from backend.app.models.annotation import Annotation
    from backend.app.models.telemetry import RunTelemetryRecord
    from backend.app.repositories.annotations import AnnotationsRepo
    from backend.app.repositories.jobs import JobsRepo
    from backend.app.repositories.prompts import PromptsRepo
    from backend.app.repositories.run_telemetry import RunTelemetryRepo

    with _make_client(monkeypatch, tmp_path) as client:
        ctx = client.app.state.core_ctx

        async def _seed() -> None:
            prompts = PromptsRepo()
            _, vid = await prompts.create_with_initial_version(
                ctx.db,
                name="p",
                description=None,
                body="b",
                target_map={},
                output_schema={},
                model="m",
            )
            jobs = JobsRepo()
            jid = await jobs.create_job(ctx.db, prompt_version_id=vid, clip_ids=[12041])
            anns = AnnotationsRepo()
            await anns.insert(
                ctx.db,
                Annotation(
                    catdv_clip_id=12041,
                    catdv_clip_name="Abramcukova_Anna_09",
                    prompt_version_id=vid,
                    job_id=jid,
                    model="m",
                    prompt_used="b",
                    raw_response={},
                    structured_output=None,
                    clip_snapshot={},
                ),
            )
            tele = RunTelemetryRepo()
            await tele.insert(
                ctx.db,
                RunTelemetryRecord(
                    occurred_at=datetime.now(UTC).isoformat(),
                    install_id="i",
                    kind="annotation",
                    model="m",
                    status="ok",
                    job_id=jid,
                    clip_id=12041,
                    cost_usd=0.21,
                ),
            )

        asyncio.run(_seed())
        install_live_ctx(client.app, archive=FakeArchive((_canonical(),)))
        r = client.get("/clips/12041")
        assert r.status_code == 200
        # $0.21 (≥$0.10 → 2 decimals); the panel labels it "Cost".
        assert "Cost: $0.21" in r.text


def test_clip_detail_404_when_missing(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        install_live_ctx(client.app, archive=FakeArchive(()))
        r = client.get("/clips/99999")
        assert r.status_code == 404


def test_static_mount(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        r = client.get("/static/app.css")
        assert r.status_code == 200
        assert "text/css" in r.headers["content-type"]


def test_clip_detail_renders_without_timeline_when_duration_zero(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        zero_dur = dataclasses.replace(_canonical(), duration_secs=0.0)
        install_live_ctx(client.app, archive=FakeArchive((zero_dur,)))
        r = client.get("/clips/12041")
        assert r.status_code == 200
        assert "Abramcukova_Anna_09" in r.text
        assert 'class="timeline"' not in r.text


class SpyListCacheRepo:
    """In-memory stand-in that records invalidate/get calls."""

    def __init__(self):
        self.invalidated: list[tuple[str, str]] = []
        self.entry: dict | None = None

    async def invalidate_catalog(self, conn, *, provider_id, catalog_id):
        self.invalidated.append((provider_id, catalog_id))
        return 0

    async def get(self, conn, *, provider_id, catalog_id, query_text, offset, limit):
        return self.entry


def test_refresh_query_invalidates_list_cache(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        ctx = client.app.state.core_ctx
        spy = SpyListCacheRepo()
        ctx.clip_list_cache_repo = spy
        install_live_ctx(client.app, archive=FakeArchive((_canonical(),)))

        r = client.get("/?refresh=1")
        assert r.status_code == 200
        assert spy.invalidated == [("catdv", "881507")]


def test_no_refresh_query_does_not_invalidate(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        ctx = client.app.state.core_ctx
        spy = SpyListCacheRepo()
        ctx.clip_list_cache_repo = spy
        install_live_ctx(client.app, archive=FakeArchive((_canonical(),)))

        r = client.get("/")
        assert r.status_code == 200
        assert spy.invalidated == []


def test_cache_age_displayed_when_entry_present(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        ctx = client.app.state.core_ctx
        spy = SpyListCacheRepo()
        # Pretend the list cache has a row 2 minutes old.
        from datetime import datetime, timedelta

        two_min_ago = (datetime.now(UTC) - timedelta(minutes=2)).isoformat()
        spy.entry = {"fetched_at": two_min_ago, "total": 1, "items": ()}
        ctx.clip_list_cache_repo = spy
        install_live_ctx(client.app, archive=FakeArchive((_canonical(),)))

        r = client.get("/")
        assert r.status_code == 200
        assert "Cached" in r.text
        assert "Refresh" in r.text
        assert "refresh=1" in r.text


def test_clips_page_marks_clips_rail_active(monkeypatch, tmp_path):
    """Clips list sets rail_active so the Clips icon gets `.active`."""
    with _make_client(monkeypatch, tmp_path) as client:
        install_live_ctx(client.app, archive=FakeArchive((_canonical(),)))
        r = client.get("/")
        assert r.status_code == 200
        # exactly one rail button should be active; the first one is Clips
        assert "rail-btn active" in r.text
        assert 'title="Clips"' in r.text
        # all three icons present
        assert "rail-preview" in r.text
        assert 'title="Cache"' in r.text


def test_clip_detail_marks_preview_rail_active(monkeypatch, tmp_path):
    """Detail page activates the Preview rail icon and writes lastClipId."""
    with _make_client(monkeypatch, tmp_path) as client:
        install_live_ctx(client.app, archive=FakeArchive((_canonical(),)))
        r = client.get("/clips/12041")
        assert r.status_code == 200
        assert "rail-btn active" in r.text
        assert 'localStorage.setItem("catdv:lastClipId", "12041")' in r.text


def test_pager_url_encodes_search_query(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        install_live_ctx(client.app, archive=FakeArchive((_canonical(),), total=100))
        r = client.get("/?q=hello+world%26x&offset=10&limit=10")
        assert r.status_code == 200
        assert "q=hello%20world%26x" in r.text
        assert "q=hello world&x" not in r.text


def test_clips_list_batch_filter_dropdown(monkeypatch, tmp_path):
    """Batch <select> renders on GET / (even with no jobs = empty dropdown)."""
    with _make_client(monkeypatch, tmp_path) as client:
        install_live_ctx(client.app, archive=FakeArchive((_canonical(),)))
        r = client.get("/")
        assert r.status_code == 200
        assert 'select name="batch"' in r.text
        # Any-batch option is always present
        assert 'name="batch"' in r.text


def test_clips_list_empty_batch_param_is_not_422(monkeypatch, tmp_path):
    """The 'Any' batch option submits batch= (empty); it must coerce to None,
    not 422. Regression for the filter form breaking on every change."""
    with _make_client(monkeypatch, tmp_path) as client:
        install_live_ctx(client.app, archive=FakeArchive((_canonical(),)))
        r = client.get("/?q=&cache=any&anno=for_review&batch=")
        assert r.status_code == 200
        # A real job id still works.
        r2 = client.get("/?cache=any&anno=for_review&batch=7")
        assert r2.status_code == 200


def test_clips_list_batch_view_shows_per_clip_cost(monkeypatch, tmp_path):
    """When the list is filtered to a batch, each clip's actual billable
    cost appears in its own Cost column <td> and the Cost <th> is present."""
    import asyncio

    from backend.app.models.telemetry import RunTelemetryRecord
    from backend.app.repositories.jobs import JobsRepo
    from backend.app.repositories.prompts import PromptsRepo
    from backend.app.repositories.run_telemetry import RunTelemetryRepo

    with _make_client(monkeypatch, tmp_path) as client:
        ctx = client.app.state.core_ctx

        async def _seed() -> int:
            prompts = PromptsRepo()
            _, vid = await prompts.create_with_initial_version(
                ctx.db,
                name="p",
                description=None,
                body="b",
                target_map={},
                output_schema={},
                model="m",
            )
            jobs = JobsRepo()
            jid = await jobs.create_job(ctx.db, prompt_version_id=vid, clip_ids=[12041])
            tele = RunTelemetryRepo()
            await tele.insert(
                ctx.db,
                RunTelemetryRecord(
                    occurred_at=datetime.now(UTC).isoformat(),
                    install_id="i",
                    kind="annotation",
                    model="m",
                    status="ok",
                    job_id=jid,
                    clip_id=12041,
                    cost_usd=0.034,
                ),
            )
            return jid

        jid = asyncio.run(_seed())
        install_live_ctx(client.app, archive=FakeArchive((_canonical(),)))
        r = client.get(f"/?batch={jid}")
        assert r.status_code == 200
        # Cost column header must be present in the batch-filtered view.
        assert "<th" in r.text and "Cost" in r.text
        # $0.034 (<$0.10 → 3 decimals) in the Cost <td> (class batch-cost).
        assert "$0.034" in r.text
        assert "batch-cost" in r.text
        # The old sub-line pattern (<div class="batch-cost muted">) is gone.
        assert '<div class="batch-cost muted">' not in r.text


def test_clips_list_running_batch_self_polls_then_stops(monkeypatch, tmp_path):
    """While a viewed batch still has in-flight items, the tbody emits a
    self-limiting poller (targeting the lightweight OOB status endpoint, not a
    full-region re-render); once every item settles, the poller is gone."""
    import asyncio

    from backend.app.repositories.jobs import JobsRepo
    from backend.app.repositories.prompts import PromptsRepo

    with _make_client(monkeypatch, tmp_path) as client:
        ctx = client.app.state.core_ctx

        async def _seed() -> int:
            prompts = PromptsRepo()
            _, vid = await prompts.create_with_initial_version(
                ctx.db, name="p", description=None, body="b",
                target_map={}, output_schema={}, model="m",
            )
            jobs = JobsRepo()
            # Fresh job → items are 'pending' (in-flight) → batch is running.
            return await jobs.create_job(ctx.db, prompt_version_id=vid, clip_ids=[12041])

        jid = asyncio.run(_seed())
        install_live_ctx(client.app, archive=FakeArchive((_canonical(),)))

        r = client.get(f"/?batch={jid}")
        assert r.status_code == 200
        assert 'id="bstatus-poll"' in r.text  # running → poller armed
        assert "/ui/batch-statuses" in r.text  # lightweight OOB endpoint
        # The poller does OOB swaps (hx-swap="none"), not a full-region re-render.
        assert 'hx-trigger="every 4s"' in r.text

        # Settle the batch: every item done → poller must disappear.
        async def _finish():
            jobs = JobsRepo()
            for it in await jobs.list_items(ctx.db, jid):
                await jobs.update_item_status(ctx.db, it.id, "review_ready")

        asyncio.run(_finish())
        r2 = client.get(f"/?batch={jid}")
        assert r2.status_code == 200
        assert 'id="bstatus-poll"' not in r2.text  # settled → no poll


def test_batch_statuses_fragment_returns_oob_pills(monkeypatch, tmp_path):
    """The lightweight status endpoint returns one OOB span per clip and, only
    once the batch settles, a triggerless poller replacement to stop polling."""
    import asyncio

    from backend.app.repositories.jobs import JobsRepo
    from backend.app.repositories.prompts import PromptsRepo

    with _make_client(monkeypatch, tmp_path) as client:
        ctx = client.app.state.core_ctx

        async def _seed() -> int:
            prompts = PromptsRepo()
            _, vid = await prompts.create_with_initial_version(
                ctx.db, name="p", description=None, body="b",
                target_map={}, output_schema={}, model="m",
            )
            jobs = JobsRepo()
            return await jobs.create_job(ctx.db, prompt_version_id=vid, clip_ids=[12041])

        jid = asyncio.run(_seed())

        # Running: OOB pill present, no poller-stop.
        r = client.get(f"/ui/batch-statuses?batch={jid}")
        assert r.status_code == 200
        assert 'id="bstatus-12041"' in r.text
        assert 'hx-swap-oob="true"' in r.text
        assert 'id="bstatus-poll"' not in r.text  # still running → keep polling

        async def _finish():
            jobs = JobsRepo()
            for it in await jobs.list_items(ctx.db, jid):
                await jobs.update_item_status(ctx.db, it.id, "review_ready")

        asyncio.run(_finish())
        # Settled: OOB poller-stop span present.
        r2 = client.get(f"/ui/batch-statuses?batch={jid}")
        assert r2.status_code == 200
        assert 'id="bstatus-poll"' in r2.text  # triggerless → polling stops


def test_topbar_to_review_chip_counts_unapplied_drafts(monkeypatch, tmp_path):
    """An un-applied draft surfaces a '👁 N to review' chip linking to the review
    queue; once applied, the chip is gone."""
    import asyncio

    from backend.app.repositories.jobs import JobsRepo
    from backend.app.repositories.prompts import PromptsRepo

    with _make_client(monkeypatch, tmp_path) as client:
        ctx = client.app.state.core_ctx

        async def _seed_unapplied() -> None:
            prompts = PromptsRepo()
            _, vid = await prompts.create_with_initial_version(
                ctx.db, name="p", description=None, body="b",
                target_map={}, output_schema={}, model="m",
            )
            jobs = JobsRepo()
            jid = await jobs.create_job(ctx.db, prompt_version_id=vid, clip_ids=[12041])
            cur = await ctx.db.execute(
                "INSERT INTO annotations "
                "(catdv_clip_id, catdv_clip_name, prompt_version_id, job_id, model, "
                " prompt_used, raw_response, structured_output, clip_snapshot, created_at) "
                "VALUES (12041, 'C', ?, ?, 'm', 'p', '{}', '{}', '{}', '2026-06-02T00:00:00')",
                (vid, jid),
            )
            ann_id = cur.lastrowid
            await ctx.db.execute(
                "INSERT INTO review_items "
                "(annotation_id, studio_run_id, catdv_clip_id, kind, target_identifier, "
                " proposed_value, edited_value, decision, applied_at) "
                "VALUES (?, NULL, 12041, 'marker', NULL, '{}', NULL, 'pending', NULL)",
                (ann_id,),
            )
            await ctx.db.commit()

        asyncio.run(_seed_unapplied())
        install_live_ctx(client.app, archive=FakeArchive((_canonical(),)))

        r = client.get("/")
        assert r.status_code == 200
        assert "to-review-chip" in r.text
        assert "1 to review" in r.text
        assert 'href="/?anno=for_review"' in r.text

        async def _apply() -> None:
            await ctx.db.execute(
                "UPDATE review_items SET applied_at = '2026-06-02T01:00:00'"
            )
            await ctx.db.commit()

        asyncio.run(_apply())
        r2 = client.get("/")
        assert r2.status_code == 200
        assert "to-review-chip" not in r2.text


def test_clips_list_normal_view_has_no_cost_column(monkeypatch, tmp_path):
    """The unfiltered clips list must NOT include the Cost column."""
    with _make_client(monkeypatch, tmp_path) as client:
        install_live_ctx(client.app, archive=FakeArchive((_canonical(),)))
        r = client.get("/")
        assert r.status_code == 200
        # No Cost <th> in the normal view.
        assert ">Cost<" not in r.text
        # No batch-cost cell either.
        assert "batch-cost" not in r.text


def test_clips_list_batch_view_no_telemetry_shows_dash(monkeypatch, tmp_path):
    """A clip in a batch view with no telemetry row shows '—' in the Cost column."""
    import asyncio

    from backend.app.repositories.jobs import JobsRepo
    from backend.app.repositories.prompts import PromptsRepo

    with _make_client(monkeypatch, tmp_path) as client:
        ctx = client.app.state.core_ctx

        async def _seed() -> int:
            prompts = PromptsRepo()
            _, vid = await prompts.create_with_initial_version(
                ctx.db,
                name="p",
                description=None,
                body="b",
                target_map={},
                output_schema={},
                model="m",
            )
            jobs = JobsRepo()
            return await jobs.create_job(ctx.db, prompt_version_id=vid, clip_ids=[12041])

        jid = asyncio.run(_seed())
        install_live_ctx(client.app, archive=FakeArchive((_canonical(),)))
        r = client.get(f"/?batch={jid}")
        assert r.status_code == 200
        # Cost column present.
        assert ">Cost<" in r.text
        # The em-dash rendered by the usd filter for None.
        assert "batch-cost" in r.text
        assert "—" in r.text
