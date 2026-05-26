import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.models.studio import AnnotationOutput
from backend.app.repositories.prompts import PromptsRepo
from backend.app.repositories.studio_runs import StudioRunsRepo
from backend.app.repositories.testbench_items import TestbenchItemsRepo
from backend.app.repositories.testbenches import TestbenchesRepo
from backend.app.services.studio_runs import StudioRunsService


@pytest.mark.asyncio
async def test_run_processes_upload_items_into_studio_run_items(db, tmp_path, monkeypatch):
    prompts = PromptsRepo()
    _, pv_id = await prompts.create_with_initial_version(
        db, name="p", description=None, body="BODY",
        target_map={}, output_schema={}, model="m", initial_state="production",
    )
    tb = await TestbenchesRepo().create(db, name="t", description=None)
    f = await TestbenchesRepo().create_folder(db, testbench_id=tb, parent_id=None, name="r")
    items_repo = TestbenchItemsRepo()
    (tmp_path / "a.mp4").write_bytes(b"\x00")
    (tmp_path / "b.mp4").write_bytes(b"\x00")
    a = await items_repo.add_upload(db, folder_id=f, upload_path="a.mp4", original_name="a.mp4")
    b = await items_repo.add_upload(db, folder_id=f, upload_path="b.mp4", original_name="b.mp4")

    async def fake_process_item(**kw):
        await kw["on_status"]("resolving")
        await kw["on_status"]("uploading")
        await kw["on_status"]("prompting")
        return AnnotationOutput(
            structured={"k": kw["clip_key"][1]},
            raw_text=json.dumps({"k": kw["clip_key"][1]}),
            raw={},
            prompt_used="BODY", model="m", latency_ms=42,
        )

    monkeypatch.setattr(
        "backend.app.services.studio_runs.process_item", fake_process_item,
    )

    svc = StudioRunsService(
        runs_repo=StudioRunsRepo(),
        items_repo=items_repo,
        prompts_repo=prompts,
        archive=None, proxy_resolver=None,
        cache_only_resolver=MagicMock(),
        clip_cache_repo=MagicMock(),
        ai_store=MagicMock(),
        gemini=MagicMock(),
        event_bus=MagicMock(publish=AsyncMock()),
        uploads_root=tmp_path,
        mode_getter=lambda: "online",
    )
    run_id = await svc.create_run(db, testbench_id=tb, prompt_version_id=pv_id)
    await svc.run(db, run_id)

    run = await StudioRunsRepo().get(db, run_id)
    assert run.status == "completed"
    items = await StudioRunsRepo().list_items(db, run_id)
    assert {it.testbench_item_id for it in items} == {a, b}
    assert all(it.status == "done" for it in items)


@pytest.mark.asyncio
async def test_run_marks_unacceptable_items(db, tmp_path):
    prompts = PromptsRepo()
    _, pv_id = await prompts.create_with_initial_version(
        db, name="p", description=None, body="BODY",
        target_map={}, output_schema={}, model="m", initial_state="production",
    )
    tb = await TestbenchesRepo().create(db, name="t", description=None)
    f = await TestbenchesRepo().create_folder(db, testbench_id=tb, parent_id=None, name="r")
    await TestbenchItemsRepo().add_catdv(
        db, folder_id=f, provider_clip_id="999", name="x"
    )

    svc = StudioRunsService(
        runs_repo=StudioRunsRepo(),
        items_repo=TestbenchItemsRepo(),
        prompts_repo=prompts,
        archive=None, proxy_resolver=None,
        cache_only_resolver=MagicMock(
            path_for_clip_id=AsyncMock(side_effect=FileNotFoundError())
        ),
        clip_cache_repo=MagicMock(),
        ai_store=MagicMock(status=AsyncMock(return_value=None)),
        gemini=MagicMock(),
        event_bus=MagicMock(publish=AsyncMock()),
        uploads_root=tmp_path,
        mode_getter=lambda: "offline",
    )
    rid = await svc.create_run(db, testbench_id=tb, prompt_version_id=pv_id)
    await svc.run(db, rid)
    items = await StudioRunsRepo().list_items(db, rid)
    assert items[0].status == "unacceptable"
    assert (await StudioRunsRepo().get(db, rid)).status == "completed"


@pytest.mark.asyncio
async def test_run_failed_when_any_item_errors(db, tmp_path, monkeypatch):
    prompts = PromptsRepo()
    _, pv_id = await prompts.create_with_initial_version(
        db, name="p", description=None, body="BODY",
        target_map={}, output_schema={}, model="m", initial_state="production",
    )
    tb = await TestbenchesRepo().create(db, name="t", description=None)
    f = await TestbenchesRepo().create_folder(db, testbench_id=tb, parent_id=None, name="r")
    (tmp_path / "a.mp4").write_bytes(b"\x00")
    await TestbenchItemsRepo().add_upload(
        db, folder_id=f, upload_path="a.mp4", original_name="a.mp4"
    )

    async def boom(**kw):
        raise RuntimeError("gemini exploded")
    monkeypatch.setattr("backend.app.services.studio_runs.process_item", boom)

    svc = StudioRunsService(
        runs_repo=StudioRunsRepo(),
        items_repo=TestbenchItemsRepo(),
        prompts_repo=prompts,
        archive=None, proxy_resolver=None,
        cache_only_resolver=MagicMock(),
        clip_cache_repo=MagicMock(),
        ai_store=MagicMock(),
        gemini=MagicMock(),
        event_bus=MagicMock(publish=AsyncMock()),
        uploads_root=tmp_path,
        mode_getter=lambda: "online",
    )
    rid = await svc.create_run(db, testbench_id=tb, prompt_version_id=pv_id)
    await svc.run(db, rid)
    items = await StudioRunsRepo().list_items(db, rid)
    assert items[0].status == "error"
    assert "gemini exploded" in (items[0].error or "")
    assert (await StudioRunsRepo().get(db, rid)).status == "failed"


@pytest.mark.asyncio
async def test_cancel_stops_at_next_item_boundary(db, tmp_path, monkeypatch):
    prompts = PromptsRepo()
    _, pv_id = await prompts.create_with_initial_version(
        db, name="p", description=None, body="BODY",
        target_map={}, output_schema={}, model="m", initial_state="production",
    )
    tb = await TestbenchesRepo().create(db, name="t", description=None)
    f = await TestbenchesRepo().create_folder(db, testbench_id=tb, parent_id=None, name="r")
    for n in ("a", "b", "c"):
        (tmp_path / f"{n}.mp4").write_bytes(b"\x00")
        await TestbenchItemsRepo().add_upload(
            db, folder_id=f, upload_path=f"{n}.mp4", original_name=f"{n}.mp4"
        )

    started: list[str] = []

    async def slow_proc(**kw):
        started.append(kw["clip_key"][1])
        return AnnotationOutput(
            structured=None, raw_text="", raw={},
            prompt_used="x", model="m", latency_ms=0,
        )

    monkeypatch.setattr("backend.app.services.studio_runs.process_item", slow_proc)

    svc = StudioRunsService(
        runs_repo=StudioRunsRepo(),
        items_repo=TestbenchItemsRepo(),
        prompts_repo=prompts,
        archive=None, proxy_resolver=None,
        cache_only_resolver=MagicMock(),
        clip_cache_repo=MagicMock(),
        ai_store=MagicMock(),
        gemini=MagicMock(),
        event_bus=MagicMock(publish=AsyncMock()),
        uploads_root=tmp_path,
        mode_getter=lambda: "online",
    )
    rid = await svc.create_run(db, testbench_id=tb, prompt_version_id=pv_id)
    await StudioRunsRepo().update_status(db, rid, "cancelled", finished=True)
    await svc.run(db, rid)
    assert started == []
    assert (await StudioRunsRepo().get(db, rid)).status == "cancelled"
