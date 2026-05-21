import pytest

from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.prompts import PromptsRepo


async def _seed_version(db) -> int:
    prompts = PromptsRepo()
    _, vid = await prompts.create_with_initial_version(
        db,
        name="t",
        description=None,
        body="p",
        target_map={"x": {"kind": "markers"}},
        output_schema={},
        model="m",
    )
    return vid


@pytest.mark.asyncio
async def test_create_job_with_items_and_progress(db):
    vid = await _seed_version(db)

    jobs = JobsRepo()
    clip_ids = [101, 102, 103]
    job_id = await jobs.create_job(db, prompt_version_id=vid, clip_ids=clip_ids)

    job = await jobs.get_job(db, job_id)
    assert job.total_clips == 3
    assert job.status == "pending"

    items = await jobs.list_items(db, job_id)
    assert [it.catdv_clip_id for it in items] == clip_ids
    assert all(it.status == "pending" for it in items)


@pytest.mark.asyncio
async def test_update_item_status(db):
    vid = await _seed_version(db)
    jobs = JobsRepo()
    job_id = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[1, 2])
    items = await jobs.list_items(db, job_id)

    await jobs.update_item_status(db, items[0].id, "resolving")
    refreshed = await jobs.list_items(db, job_id)
    assert refreshed[0].status == "resolving"


@pytest.mark.asyncio
async def test_reset_transient_statuses_on_recovery(db):
    vid = await _seed_version(db)
    jobs = JobsRepo()
    job_id = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[1, 2, 3])
    items = await jobs.list_items(db, job_id)
    await jobs.update_item_status(db, items[0].id, "uploading")
    await jobs.update_item_status(db, items[1].id, "prompting")
    await jobs.update_item_status(db, items[2].id, "review_ready")

    reset_count = await jobs.reset_transient(db)
    assert reset_count == 2
    refreshed = await jobs.list_items(db, job_id)
    statuses = sorted(it.status for it in refreshed)
    assert statuses == ["pending", "pending", "review_ready"]
