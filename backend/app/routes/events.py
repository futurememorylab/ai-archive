"""SSE event-stream routes — exposes the EventBus over Server-Sent Events
for job, prefetch, and connection topics."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import aiosqlite
from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from backend.app.deps import get_core_ctx
from backend.app.repositories.jobs import JobsRepo
from backend.app.services.events import EventBus

router = APIRouter(tags=["events"])

# An idle stream only notices a gone client when the next event arrives, so
# without a cap every page view pins a Cloud Run request slot for the full
# 300s timeout: old revisions keep running for minutes after a deploy and a
# saturated instance makes Cloud Run start a second one (two SQLite copies).
# Ending the stream lets EventSource reconnect on its own.
STREAM_MAX_AGE_S = 30.0


async def _next_or_none(q: asyncio.Queue, deadline: float) -> Any | None:
    """Next queued payload, or None once the stream's deadline has passed."""
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        return None
    try:
        return await asyncio.wait_for(q.get(), remaining)
    except TimeoutError:
        return None


async def _event_generator(
    bus: EventBus,
    *,
    topic: str,
    close_after: int | None = None,
    max_age_s: float = STREAM_MAX_AGE_S,
) -> AsyncIterator[str]:
    q = bus.subscribe(topic)
    deadline = asyncio.get_running_loop().time() + max_age_s
    try:
        emitted = 0
        while True:
            payload = await _next_or_none(q, deadline)
            if payload is None:
                return
            yield f"data: {json.dumps(payload)}\n\n"
            emitted += 1
            if close_after is not None and emitted >= close_after:
                return
    finally:
        bus.unsubscribe(topic, q)


async def _replay_frames(
    jobs_repo: JobsRepo, db: aiosqlite.Connection, job_id: int
) -> list[dict[str, Any]]:
    """Current per-item status frames for a late-subscribing client.

    The annotator emits `resolving`/`uploading` within milliseconds of a job
    auto-starting, but the browser's EventSource subscribes via a separate
    request that lands later — and EventBus has no replay, so those early
    frames are lost and the "Caching…" feedback never shows (issue #58). On
    connect we read the items' current status and hand them back so a client
    that joins mid-upload still sees the phase. `pending` items haven't started
    and carry no signal, so they're skipped.
    """
    frames: list[dict[str, Any]] = []
    for item in await jobs_repo.list_items(db, job_id):
        if item.status == "pending":
            continue
        frame: dict[str, Any] = {"item_id": item.id, "status": item.status}
        if item.status == "error" and item.error_message:
            frame["error"] = item.error_message
        if item.annotation_id is not None:
            frame["annotation_id"] = item.annotation_id
        frames.append(frame)
    return frames


@router.get("/api/jobs/{job_id}/events")
async def job_events(request: Request, job_id: int):
    ctx = get_core_ctx(request)
    topic = f"job:{job_id}"

    async def stream():
        # Subscribe BEFORE the replay read so any frame published in the gap
        # is queued (and delivered after replay) rather than lost — replay
        # closes the pre-subscribe race, the queue closes the post-read one.
        bus = ctx.event_bus
        q = bus.subscribe(topic)
        deadline = asyncio.get_running_loop().time() + STREAM_MAX_AGE_S
        try:
            for frame in await _replay_frames(ctx.jobs_repo, ctx.db, job_id):
                if await request.is_disconnected():
                    return
                yield {"data": json.dumps(frame)}
            while True:
                payload = await _next_or_none(q, deadline)
                if payload is None or await request.is_disconnected():
                    return
                yield {"data": json.dumps(payload)}
        finally:
            bus.unsubscribe(topic, q)

    return EventSourceResponse(stream())
