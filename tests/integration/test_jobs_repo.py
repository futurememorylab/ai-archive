import pytest

from backend.app.models.job import Job
from backend.app.models.template import Template
from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.templates import TemplatesRepo


@pytest.mark.asyncio
async def test_create_job_with_items_and_progress(db):
    templates = TemplatesRepo()
    template_id = await templates.create(db, Template(
        name="t", prompt="p", output_schema={}, target_map={"x": {"kind": "markers"}}, model="m"
    ))

    jobs = JobsRepo()
    clip_ids = [101, 102, 103]
    job_id = await jobs.create_job(db, template_id=template_id, clip_ids=clip_ids)

    job = await jobs.get_job(db, job_id)
    assert job.total_clips == 3
    assert job.status == "pending"

    items = await jobs.list_items(db, job_id)
    assert [it.catdv_clip_id for it in items] == clip_ids
    assert all(it.status == "pending" for it in items)


@pytest.mark.asyncio
async def test_update_item_status(db):
    templates = TemplatesRepo()
    t = await templates.create(db, Template(
        name="t", prompt="p", output_schema={}, target_map={"x": {"kind": "markers"}}, model="m"
    ))
    jobs = JobsRepo()
    job_id = await jobs.create_job(db, template_id=t, clip_ids=[1, 2])
    items = await jobs.list_items(db, job_id)

    await jobs.update_item_status(db, items[0].id, "resolving")
    refreshed = await jobs.list_items(db, job_id)
    assert refreshed[0].status == "resolving"


@pytest.mark.asyncio
async def test_reset_transient_statuses_on_recovery(db):
    templates = TemplatesRepo()
    t = await templates.create(db, Template(
        name="t", prompt="p", output_schema={}, target_map={"x": {"kind": "markers"}}, model="m"
    ))
    jobs = JobsRepo()
    job_id = await jobs.create_job(db, template_id=t, clip_ids=[1, 2, 3])
    items = await jobs.list_items(db, job_id)
    await jobs.update_item_status(db, items[0].id, "uploading")
    await jobs.update_item_status(db, items[1].id, "prompting")
    await jobs.update_item_status(db, items[2].id, "review_ready")

    reset_count = await jobs.reset_transient(db)
    assert reset_count == 2
    refreshed = await jobs.list_items(db, job_id)
    statuses = sorted(it.status for it in refreshed)
    assert statuses == ["pending", "pending", "review_ready"]
