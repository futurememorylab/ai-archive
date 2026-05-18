import asyncio
import json

import pytest

from backend.app.routes.events import _event_generator
from backend.app.services.events import EventBus


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
