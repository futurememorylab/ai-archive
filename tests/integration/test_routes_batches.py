import asyncio
import importlib
from datetime import UTC, datetime
from unittest.mock import MagicMock

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
        ctx.db, name="Scénické značky CZ", description=None, body="p",
        target_map={"x": {"kind": "markers"}}, output_schema={}, model="gemini-2.5-pro",
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
        asyncio.run(_seed_batch(client.app.state.core_ctx))
        r = client.get("/batches")
        assert r.status_code == 200
        assert "<!doctype html>" in r.text.lower()
        assert "Scénické značky CZ" in r.text
        assert "gemini-2.5-pro" in r.text
        # rail marks Batches active
        assert 'title="Batches"' in r.text
        assert "rail-btn active" in r.text
        # failed count surfaced
        assert "1 failed" in r.text
        # actual batch cost surfaced in its own <td> ($0.12 → 2 decimals via the usd filter)
        assert "$0.12" in r.text
        assert 'class="bt-cost mono"' in r.text


def test_batches_table_partial(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        asyncio.run(_seed_batch(client.app.state.core_ctx))
        r = client.get("/batches/table")
        assert r.status_code == 200
        assert "<!doctype html>" not in r.text.lower()
        assert "Scénické značky CZ" in r.text
        # The whole row is clickable → the batch's full file list (?batch=…).
        assert "location.href='/?batch=" in r.text
        assert 'class="batch-row"' in r.text


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
                ctx.db, name="No-cost batch", description=None, body="p",
                target_map={"x": {"kind": "markers"}}, output_schema={}, model="gemini-2.5-pro",
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
        assert statuses[102] == "pending"        # was 'error' → reset
        assert statuses[101] == "review_ready"   # untouched
        assert job_status == "pending"           # job requeued for the worker


def _picker_clip(clip_id=12041, name="Abramcukova_Anna_09", file_path=None):
    # Kind is derived from the media.filePath extension at render time; default
    # to a .mov (→ video) so existing tests stay "video".
    media_path = file_path if file_path is not None else f"/vol/{name}.mov"
    return CanonicalClip(
        key=("catdv", str(clip_id)), name=name, duration_secs=60.0, fps=25.0,
        markers=(), fields={}, notes={},
        media=MediaRef(mime_type="video/quicktime", size_bytes=None,
                       cached_path=None, upstream_handle=str(clip_id)),
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
        return ClipPage(items=self._clips[s:s + query.limit], total=self._total,
                        offset=query.offset, limit=query.limit)

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
        assert 'value="catdv/12041"' in r.text          # selection checkbox
        assert "Abramcukova_Anna_09" in r.text
        assert 'id="nb-list-meta"' in r.text             # pager meta for the client
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
        assert 'value="catdv/2"' in r.text      # the .jpg
        assert 'value="catdv/1"' not in r.text   # the .mov filtered out
        assert 'data-total="1"' in r.text        # total reflects the filtered set


def test_batches_picker_kind_video_returns_only_videos(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        install_live_ctx(client.app, archive=_PickerArchive(_kind_clips()))
        r = client.get("/batches/picker?kind=video")
        assert r.status_code == 200
        assert 'value="catdv/1"' in r.text       # the .mov
        assert 'value="catdv/2"' not in r.text   # the .jpg filtered out
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
