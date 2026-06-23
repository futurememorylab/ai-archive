"""Repo support for annotate-cache queue consistency (spec
2026-06-23-annotate-cache-queue-consistency):

- PrefetchQueueRepo.start_inline: the annotator caches inline (not via the
  worker), so it needs a row born `downloading` that claim_next never grabs.
- JobsRepo.find_running_item_for_clip: powers GET /api/jobs/active-for-clip,
  so the clip page can resume the annotate button after a reload.
"""
import pytest

from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.prefetch_queue import PrefetchQueueRepo
from backend.app.repositories.prompts import PromptsRepo


@pytest.mark.asyncio
async def test_start_inline_inserts_downloading_row_not_claimable(db):
    repo = PrefetchQueueRepo()
    rid = await repo.start_inline(db, key=("catdv", "42"), who="annotate")
    row = await repo.get(db, rid)
    assert row["status"] == "downloading"
    assert row["started_at"] is not None
    assert row["requested_by"] == "annotate"
    # Born downloading → the single-worker claim_next must never grab it
    # (that would double-download the clip the annotator is already pulling).
    assert await repo.claim_next(db) is None


@pytest.mark.asyncio
async def test_start_inline_is_idempotent_with_active_row(db):
    repo = PrefetchQueueRepo()
    # An existing active row (e.g. the user also clicked Cache) is reused.
    queued = await repo.enqueue(db, key=("catdv", "7"), who="request")
    again = await repo.start_inline(db, key=("catdv", "7"), who="annotate")
    assert again == queued


async def _seed_running_job(db, *, clip_id, item_status="uploading"):
    prompts = PromptsRepo()
    _pid, vid = await prompts.create_with_initial_version(
        db, name="P", description=None, body="b",
        target_map={"x": {"kind": "markers"}}, output_schema={}, model="m",
    )
    jobs = JobsRepo()
    job_id = await jobs.create_job(db, prompt_version_id=vid, clip_ids=[clip_id])
    await jobs.update_status(db, job_id, "running")
    items = await jobs.list_items(db, job_id)
    await jobs.update_item_status(db, items[0].id, item_status)
    return job_id


@pytest.mark.asyncio
async def test_find_running_item_for_clip_returns_job_and_status(db):
    jobs = JobsRepo()
    job_id = await _seed_running_job(db, clip_id=99, item_status="uploading")
    found = await jobs.find_running_item_for_clip(db, 99)
    assert found["job_id"] == job_id
    assert found["item_status"] == "uploading"
    # started_at (the job's created_at) lets the button resume its elapsed timer.
    assert found["started_at"]


@pytest.mark.asyncio
async def test_find_running_item_for_clip_none_when_not_running(db):
    jobs = JobsRepo()
    job_id = await _seed_running_job(db, clip_id=123, item_status="prompting")
    await jobs.update_status(db, job_id, "completed")
    assert await jobs.find_running_item_for_clip(db, 123) is None
    # And None for a clip no job ever touched.
    assert await jobs.find_running_item_for_clip(db, 555) is None
