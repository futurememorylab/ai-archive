import asyncio
import json

import pytest

from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.routes.events import _event_generator, _replay_frames
from backend.app.services.events import EventBus


async def _seed_prompt_version(db) -> int:
    _, vid = await PromptsRepo().create_with_initial_version(
        db,
        name="t",
        description=None,
        body="describe scenes",
        target_map={"scenes": {"kind": "markers"}},
        output_schema={"type": "object"},
        model="gemini-2.5-flash-lite",
    )
    return vid


@pytest.mark.asyncio
async def test_event_generator_yields_sse_frames():
    bus = EventBus()
    gen = _event_generator(bus, topic="job:7", close_after=2)

    async def publish():
        await asyncio.sleep(0.01)
        await bus.publish("job:7", {"item_id": 1, "status": "uploading"})
        await bus.publish("job:7", {"item_id": 1, "status": "review_ready"})

    publisher = asyncio.create_task(publish())

    received = []
    async for chunk in gen:
        received.append(chunk)
        if len(received) >= 2:
            break
    await publisher

    parsed = [json.loads(c.removeprefix("data: ").strip()) for c in received]
    assert parsed[0]["status"] == "uploading"
    assert parsed[1]["status"] == "review_ready"


# --- replay-on-connect (issue #58) -----------------------------------------
# The annotator emits `resolving`/`uploading` within milliseconds of a job
# auto-starting, but the browser's EventSource subscribes via a *separate*
# request that lands later — and EventBus has no replay, so those early frames
# are lost and the "Caching…" feedback never shows. _replay_frames closes the
# race: a client that connects mid-upload still gets the current item status.


@pytest.mark.asyncio
async def test_replay_emits_current_uploading_status(db):
    repo = JobsRepo()
    job_id = await repo.create_job(db, prompt_version_id=await _seed_prompt_version(db), clip_ids=[888894])
    item = (await repo.list_items(db, job_id))[0]
    await repo.update_item_status(db, item.id, "uploading")

    frames = await _replay_frames(repo, db, job_id)
    assert frames == [{"item_id": item.id, "status": "uploading"}]


@pytest.mark.asyncio
async def test_replay_skips_pending_items(db):
    # A job whose item hasn't started yet has nothing to replay — don't emit
    # noise frames the frontend would ignore anyway.
    repo = JobsRepo()
    job_id = await repo.create_job(db, prompt_version_id=await _seed_prompt_version(db), clip_ids=[1])
    assert await _replay_frames(repo, db, job_id) == []


@pytest.mark.asyncio
async def test_replay_carries_error_and_annotation_id(db):
    repo = JobsRepo()
    job_id = await repo.create_job(db, prompt_version_id=await _seed_prompt_version(db), clip_ids=[1, 2])
    items = await repo.list_items(db, job_id)
    await repo.update_item_status(db, items[0].id, "error", error="boom")
    await repo.update_item_status(db, items[1].id, "review_ready")

    frames = await _replay_frames(repo, db, job_id)
    by_item = {f["item_id"]: f for f in frames}
    assert by_item[items[0].id] == {
        "item_id": items[0].id,
        "status": "error",
        "error": "boom",
    }
    assert by_item[items[1].id]["status"] == "review_ready"
