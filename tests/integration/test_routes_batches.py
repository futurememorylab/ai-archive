import asyncio
import importlib
from datetime import UTC, datetime
from typing import cast
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.archive.errors import ProviderError
from backend.app.archive.model import (
    CanonicalClip,
    ClipPage,
    ClipQuery,
    MediaRef,
)
from backend.app.models.telemetry import RunTelemetryRecord
from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.repositories.run_telemetry import RunTelemetryRepo
from tests._helpers.live_ctx import install_live_ctx


def _make_client(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "7")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    return TestClient(main_mod.app)


async def _seed_batch(ctx):
    prompts = PromptsRepo()
    _, vid = await prompts.create_with_initial_version(
        ctx.db,
        name="Scénické značky CZ",
        description=None,
        body="p",
        target_map={"x": {"kind": "markers"}},
        output_schema={},
        model="gemini-2.5-pro",
    )
    jobs = JobsRepo()
    jid = await jobs.create_job(
        ctx.db, prompt_version_id=vid, clip_ids=[101, 102], run_group="rg-1"
    )
    its = await jobs.list_items(ctx.db, jid)
    await jobs.update_item_status(ctx.db, its[0].id, "review_ready")
    await jobs.update_item_status(ctx.db, its[1].id, "error", error="ProxyNotFound")
    tele = RunTelemetryRepo()
    await tele.insert(
        ctx.db,
        RunTelemetryRecord(
            occurred_at=datetime.now(UTC).isoformat(),
            install_id="inst-1",
            kind="annotation",
            model="gemini-2.5-pro",
            status="ok",
            job_id=jid,
            clip_id=101,
            cost_usd=0.12,
        ),
    )
    return jid


def test_batches_page_renders(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        jid = asyncio.run(_seed_batch(client.app.state.core_ctx))
        r = client.get("/batches")
        assert r.status_code == 200
        assert "<!doctype html>" in r.text.lower()
        assert "Scénické značky CZ" in r.text
        assert "gemini-2.5-pro" in r.text
        # rail marks Batches active
        assert 'title="Batches"' in r.text
        assert "rail-btn active" in r.text
        # failed count surfaced as the headline; the batch needs the user
        assert "1 failed" in r.text
        assert "<h2>To review</h2>" in r.text
        # actual batch cost surfaced in the sub-line ($0.12 via the usd filter)
        assert "$0.12" in r.text
        assert 'class="bt-cost mono"' in r.text
        # unnamed batch (created straight through the repo) falls back to its id
        assert f"Batch #{jid}" in r.text
        # the redesign drops the metric strip
        assert "metric-strip" not in r.text


def test_batches_table_partial(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        asyncio.run(_seed_batch(client.app.state.core_ctx))
        r = client.get("/batches/table")
        assert r.status_code == 200
        assert "<!doctype html>" not in r.text.lower()
        assert "Scénické značky CZ" in r.text
        assert 'class="batch-row"' in r.text
        # Show clips lazy-loads the batch's clip list into the row.
        assert 'data-clips-url="/batches/clips?job_ids=' in r.text
        # The thumbnail stack uses the search list's lazy .thumb images.
        assert 'class="thumb" loading="lazy" src="/api/media/101/thumb"' in r.text
        assert 'src="/api/media/102/thumb"' in r.text


def test_batches_page_empty_state(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        r = client.get("/batches")
        assert r.status_code == 200
        assert "No batches yet" in r.text


def test_batches_cost_column_shows_dash_without_telemetry(monkeypatch, tmp_path):
    """A batch with no telemetry rows renders '—' in the Cost column."""
    with _make_client(monkeypatch, tmp_path) as client:
        ctx = client.app.state.core_ctx

        async def _seed_no_tele(ctx):
            prompts = PromptsRepo()
            _, vid = await prompts.create_with_initial_version(
                ctx.db,
                name="No-cost batch",
                description=None,
                body="p",
                target_map={"x": {"kind": "markers"}},
                output_schema={},
                model="gemini-2.5-pro",
            )
            jobs = JobsRepo()
            await jobs.create_job(
                ctx.db, prompt_version_id=vid, clip_ids=[200], run_group="rg-notele"
            )

        asyncio.run(_seed_no_tele(ctx))
        r = client.get("/batches/table")
        assert r.status_code == 200
        # em-dash rendered by the usd filter for None cost_usd
        assert "—" in r.text


def test_retry_failed_503_when_offline(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        jid = asyncio.run(_seed_batch(client.app.state.core_ctx))
        # No live_ctx installed → get_live_ctx raises 503
        r = client.post("/batches/retry-failed", json={"job_ids": [jid]})
        assert r.status_code == 503


def test_retry_failed_requeues_only_jobs_with_failures(monkeypatch, tmp_path):
    """Retry is a pure DB writer now (ADR 0125): it flips the job back to
    'pending' so the lifespan JobRunner re-claims it. Only jobs that actually
    have failed items are requeued."""
    with _make_client(monkeypatch, tmp_path) as client:
        ctx = client.app.state.core_ctx
        jid = asyncio.run(_seed_batch(ctx))
        install_live_ctx(client.app, proxy_resolver=MagicMock())  # online + resolver present

        r = client.post("/batches/retry-failed", json={"job_ids": [jid]})
        assert r.status_code == 200
        assert r.json()["started"] == [jid]

        async def _job_status():
            return (await JobsRepo().get_job(ctx.db, jid)).status

        assert asyncio.run(_job_status()) == "pending"  # requeued for the worker


def test_retry_failed_flips_failed_items_and_job_to_pending(monkeypatch, tmp_path):
    """Retry must reset the failed items to 'pending' (so the batch immediately
    reads as running, in_flight > 0, instead of a stale 'Failed') AND flip the
    job to 'pending' so the JobRunner re-claims it. run_job only re-processes
    pending/error items, so only the reset clip actually re-runs."""
    with _make_client(monkeypatch, tmp_path) as client:
        ctx = client.app.state.core_ctx
        jid = asyncio.run(_seed_batch(ctx))
        install_live_ctx(client.app, proxy_resolver=MagicMock())

        r = client.post("/batches/retry-failed", json={"job_ids": [jid]})
        assert r.status_code == 200

        async def _state():
            jobs = JobsRepo()
            statuses = {it.catdv_clip_id: it.status for it in await jobs.list_items(ctx.db, jid)}
            return statuses, (await jobs.get_job(ctx.db, jid)).status

        statuses, job_status = asyncio.run(_state())
        assert statuses[102] == "pending"  # was 'error' → reset
        assert statuses[101] == "review_ready"  # untouched
        assert job_status == "pending"  # job requeued for the worker


async def _seed_stopped_batch(ctx):
    """One finished clip and two the user stopped with Cancel."""
    prompts = PromptsRepo()
    _, vid = await prompts.create_with_initial_version(
        ctx.db,
        name="Obraz",
        description=None,
        body="p",
        target_map={"x": {"kind": "markers"}},
        output_schema={},
        model="gemini-2.5-pro",
    )
    jobs = JobsRepo()
    jid = await jobs.create_job(
        ctx.db, prompt_version_id=vid, clip_ids=[201, 202, 203], run_group="rg-s"
    )
    its = await jobs.list_items(ctx.db, jid)
    await jobs.update_item_status(ctx.db, its[0].id, "review_ready")
    await jobs.cancel_job(ctx.db, jid)
    return jid


def test_stopped_batch_offers_restart(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        asyncio.run(_seed_stopped_batch(client.app.state.core_ctx))
        r = client.get("/batches/table")
        assert r.status_code == 200
        assert "Restart 2 stopped" in r.text


def test_restart_503_when_offline(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        jid = asyncio.run(_seed_stopped_batch(client.app.state.core_ctx))
        r = client.post("/batches/restart", json={"job_ids": [jid]})
        assert r.status_code == 503


def test_restart_requeues_only_the_stopped_clips(monkeypatch, tmp_path):
    """Restart flips the stopped (cancelled) clips and their job back to
    'pending' so the JobRunner re-claims the job; clips that already ran keep
    their results. A job with nothing stopped is left alone."""
    with _make_client(monkeypatch, tmp_path) as client:
        ctx = client.app.state.core_ctx
        jid = asyncio.run(_seed_stopped_batch(ctx))
        untouched = asyncio.run(_seed_batch(ctx))
        install_live_ctx(client.app, proxy_resolver=MagicMock())

        r = client.post("/batches/restart", json={"job_ids": [jid, untouched]})
        assert r.status_code == 200
        assert r.json()["started"] == [jid]

        async def _state():
            jobs = JobsRepo()
            statuses = {it.catdv_clip_id: it.status for it in await jobs.list_items(ctx.db, jid)}
            return statuses, (await jobs.get_job(ctx.db, jid)).status

        statuses, job_status = asyncio.run(_state())
        assert statuses == {201: "review_ready", 202: "pending", 203: "pending"}
        assert job_status == "pending"


def _picker_clip(clip_id=12041, name="Abramcukova_Anna_09", file_path=None):
    # Kind is derived from the media.filePath extension at render time; default
    # to a .mov (→ video) so existing tests stay "video".
    media_path = file_path if file_path is not None else f"/vol/{name}.mov"
    return CanonicalClip(
        key=("catdv", str(clip_id)),
        name=name,
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
        provider_data={"ID": clip_id, "name": name, "media": {"filePath": media_path}},
        fetched_at=datetime.now(UTC),
    )


class _PickerArchive:
    def __init__(self, clips, total=None):
        self._clips = clips
        self._total = total if total is not None else len(clips)
        self.last_query = None

    async def list_clips(self, catalog, query: ClipQuery):
        self.last_query = query
        s = query.offset
        return ClipPage(
            items=self._clips[s : s + query.limit],
            total=self._total,
            offset=query.offset,
            limit=query.limit,
        )

    async def get_clip(self, clip_id_str):
        for c in self._clips:
            if c.key[1] == clip_id_str:
                return c
        raise ProviderError("not found")


def test_batches_picker_renders_rows(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        install_live_ctx(client.app, archive=_PickerArchive([_picker_clip()]))
        r = client.get("/batches/picker")
        assert r.status_code == 200
        assert "<!doctype html>" not in r.text.lower()
        assert 'class="vlist"' in r.text
        assert 'value="catdv/12041"' in r.text  # selection checkbox
        assert "Abramcukova_Anna_09" in r.text
        assert 'id="nb-list-meta"' in r.text  # pager meta for the client
        assert 'data-total="1"' in r.text


def test_batches_picker_503_when_offline(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        # No live_ctx installed → get_live_ctx raises 503.
        r = client.get("/batches/picker")
        assert r.status_code == 503


def test_batches_picker_passes_query_and_paging(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        arch = _PickerArchive([_picker_clip(i, f"Clip_{i}") for i in range(1, 30)], total=29)
        install_live_ctx(client.app, archive=arch)
        r = client.get("/batches/picker?q=Clip&offset=12&limit=12")
        assert r.status_code == 200
        assert arch.last_query.text == "Clip"
        assert arch.last_query.offset == 12
        assert arch.last_query.limit == 12
        assert 'data-total="29"' in r.text


def _kind_clips():
    return [
        _picker_clip(1, "Vid_one", file_path="/vol/Vid_one.mov"),
        _picker_clip(2, "Img_one", file_path="/vol/Img_one.jpg"),
    ]


def test_batches_picker_kind_image_returns_only_images(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        install_live_ctx(client.app, archive=_PickerArchive(_kind_clips()))
        r = client.get("/batches/picker?kind=image")
        assert r.status_code == 200
        assert 'value="catdv/2"' in r.text  # the .jpg
        assert 'value="catdv/1"' not in r.text  # the .mov filtered out
        assert 'data-total="1"' in r.text  # total reflects the filtered set


def test_batches_picker_kind_video_returns_only_videos(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        install_live_ctx(client.app, archive=_PickerArchive(_kind_clips()))
        r = client.get("/batches/picker?kind=video")
        assert r.status_code == 200
        assert 'value="catdv/1"' in r.text  # the .mov
        assert 'value="catdv/2"' not in r.text  # the .jpg filtered out
        assert 'data-total="1"' in r.text


def test_batches_picker_no_kind_returns_both(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        install_live_ctx(client.app, archive=_PickerArchive(_kind_clips()))
        # No kind, and kind=any, both return the full set unchanged.
        for url in ("/batches/picker", "/batches/picker?kind=any"):
            r = client.get(url)
            assert r.status_code == 200
            assert 'value="catdv/1"' in r.text
            assert 'value="catdv/2"' in r.text
            assert 'data-total="2"' in r.text


# --- Batch names + contents (docs/specs/2026-10-17-batches-redesign-design.md)


def test_rename_batch_renames_and_rejects_blank(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        ctx = client.app.state.core_ctx
        jid = asyncio.run(_seed_batch(ctx))

        r = client.post("/batches/rename", json={"job_ids": [jid], "name": "  Tatry 1934 "})
        assert r.status_code == 200
        assert r.json() == {"name": "Tatry 1934"}
        assert "Tatry 1934" in client.get("/batches/table").text

        r = client.post("/batches/rename", json={"job_ids": [jid], "name": "   "})
        assert r.status_code == 422
        assert "Tatry 1934" in client.get("/batches/table").text


def _core_ctx(client: TestClient):
    """The app's core context; TestClient types `.app` as its ASGI wrapper."""
    return cast(FastAPI, client.app).state.core_ctx


def test_delete_batch_removes_the_row_and_keeps_drafts(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        ctx = _core_ctx(client)
        jid = asyncio.run(_seed_batch(ctx))
        assert f"Batch #{jid}" in client.get("/batches/table").text

        r = client.post("/batches/delete", json={"job_ids": [jid]})
        assert r.status_code == 200
        assert r.json() == {"deleted": 1}
        assert "No batches yet" in client.get("/batches/table").text


def test_delete_batch_refuses_while_running(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        ctx = _core_ctx(client)

        async def _seed_running():
            _, vid = await PromptsRepo().create_with_initial_version(
                ctx.db,
                name="Obraz",
                description=None,
                body="p",
                target_map={"x": {"kind": "markers"}},
                output_schema={},
                model="gemini-2.5-pro",
            )
            return await JobsRepo().create_job(ctx.db, prompt_version_id=vid, clip_ids=[301])

        jid = asyncio.run(_seed_running())
        r = client.post("/batches/delete", json={"job_ids": [jid]})
        assert r.status_code == 409
        assert "Cancel" in r.json()["detail"]
        assert f"Batch #{jid}" in client.get("/batches/table").text


def test_batch_row_has_one_main_action_and_a_more_menu(monkeypatch, tmp_path):
    """A row shows one main action; the rest (and Delete) sit in its ⋯ menu.
    The whole row toggles its clip list, so there's no Show clips button."""
    with _make_client(monkeypatch, tmp_path) as client:
        asyncio.run(_seed_batch(_core_ctx(client)))
        html = client.get("/batches/table").text
        assert html.count('data-test="batch-main-action"') == 1
        # Nothing to review, so retrying the failed clip is the main action.
        assert ">Retry 1 failed</button>" in html
        assert html.count("Retry 1 failed") == 1  # not repeated in the menu
        assert 'data-test="batch-more"' in html
        assert "Delete batch" in html
        assert ">Show clips<" not in html
        assert 'class="br-caret" data-test="batch-toggle"' in html
        assert '@click="rowClick($event,' in html


def test_progress_bar_only_when_the_batch_is_mixed(monkeypatch, tmp_path):
    """The bar and legend break a batch down by state; a batch in one state
    shows only its headline."""
    with _make_client(monkeypatch, tmp_path) as client:
        ctx = _core_ctx(client)

        async def _seed_all_failed():
            _, vid = await PromptsRepo().create_with_initial_version(
                ctx.db,
                name="Popis",
                description=None,
                body="p",
                target_map={"x": {"kind": "markers"}},
                output_schema={},
                model="gemini-2.5-pro",
            )
            jobs = JobsRepo()
            jid = await jobs.create_job(ctx.db, prompt_version_id=vid, clip_ids=[401])
            for it in await jobs.list_items(ctx.db, jid):
                assert it.id is not None
                await jobs.update_item_status(ctx.db, it.id, "error", error="boom")

        asyncio.run(_seed_all_failed())
        html = client.get("/batches/table").text
        assert 'data-test="batch-headline"' in html
        assert 'class="bbar"' not in html

        asyncio.run(_seed_stopped_batch(ctx))  # one done, two stopped
        assert 'class="bbar"' in client.get("/batches/table").text


def _seed_big_batch(ctx, n):
    async def go():
        _, vid = await PromptsRepo().create_with_initial_version(
            ctx.db,
            name="big",
            description=None,
            body="p",
            target_map={"x": {"kind": "markers"}},
            output_schema={},
            model="m",
        )
        return await JobsRepo().create_job(
            ctx.db,
            prompt_version_id=vid,
            clip_ids=list(range(1, n + 1)),
            batch_name="Tatry 1934",
        )

    return asyncio.run(go())


def test_thumbnail_stack_caps_at_five_plus_a_count(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        _seed_big_batch(client.app.state.core_ctx, 8)
        html = client.get("/batches/table").text
        assert "Tatry 1934" in html
        assert html.count('class="thumb" loading="lazy"') == 5
        assert "/api/media/6/thumb" not in html
        assert ">+3<" in html
        assert "8 clips" in html


def test_thumbnail_stack_shows_six_clips_in_full(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        _seed_big_batch(client.app.state.core_ctx, 6)
        html = client.get("/batches/table").text
        assert html.count('class="thumb" loading="lazy"') == 6
        assert "bstack-more" not in html


def test_batch_clips_lists_every_clip_with_its_status(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        ctx = client.app.state.core_ctx
        jid = asyncio.run(_seed_batch(ctx))
        r = client.get(f"/batches/clips?job_ids={jid}")
        assert r.status_code == 200
        html = r.text
        # the shared search-page clip table, without the selection column
        assert 'class="vlist"' in html
        assert "row-select-all" not in html
        assert "location.href='/clips/102'" in html
        assert "Clip 102" in html
        assert "ProxyNotFound" in html
        assert '<th class="col-berror">Note</th>' in html
        # what needs the user first: the failed clip before the finished one
        assert html.index("Clip 102") < html.index("Clip 101")
        assert "Open all" not in html  # small batch: nothing hidden


def test_batch_clips_caps_long_batches_with_a_search_link(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        jid = _seed_big_batch(client.app.state.core_ctx, 60)
        html = client.get(f"/batches/clips?job_ids={jid}").text
        assert html.count('class="vrow"') == 50
        assert "Open all 60 in search" in html
        assert f'href="/?batch={jid}"' in html
