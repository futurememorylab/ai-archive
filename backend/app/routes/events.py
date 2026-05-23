"""SSE event-stream routes — exposes the EventBus over Server-Sent Events
for job, prefetch, and connection topics."""

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from backend.app.deps import get_ctx
from backend.app.services.events import EventBus

router = APIRouter(tags=["events"])


async def _event_generator(
    bus: EventBus, *, topic: str, close_after: int | None = None
) -> AsyncIterator[str]:
    q = bus.subscribe(topic)
    try:
        emitted = 0
        while True:
            payload = await q.get()
            yield f"data: {json.dumps(payload)}\n\n"
            emitted += 1
            if close_after is not None and emitted >= close_after:
                return
    finally:
        bus.unsubscribe(topic, q)


@router.get("/api/jobs/{job_id}/events")
async def job_events(request: Request, job_id: int):
    ctx = get_ctx(request)
    topic = f"job:{job_id}"

    async def stream():
        async for frame in _event_generator(ctx.event_bus, topic=topic):
            if await request.is_disconnected():
                return
            yield {"data": frame.removeprefix("data: ").rstrip("\n")}

    return EventSourceResponse(stream())
