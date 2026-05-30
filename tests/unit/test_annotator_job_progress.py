import pytest

from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.services.annotator import publish_job_progress
from backend.app.services.events import EventBus


async def _seed_job(db) -> int:
    prompts = PromptsRepo()
    _, vid = await prompts.create_with_initial_version(
        db, name="t", description=None, body="p",
        target_map={"x": {"kind": "markers"}}, output_schema={}, model="m",
    )
    jobs = JobsRepo()
    return await jobs.create_job(db, prompt_version_id=vid, clip_ids=[1, 2])


@pytest.mark.asyncio
async def test_publish_job_progress_emits_to_global_topic(db):
    job_id = await _seed_job(db)
    bus = EventBus()
    q = bus.subscribe("jobs")

    await publish_job_progress(bus, JobsRepo(), db, job_id, status="running")

    payload = q.get_nowait()
    assert payload["job_id"] == job_id
    assert payload["status"] == "running"
    assert payload["total"] == 2
    assert payload["done"] == 0
    assert payload["errors"] == 0
