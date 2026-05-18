import asyncio

import pytest

from backend.app.services.events import EventBus


@pytest.mark.asyncio
async def test_subscribers_receive_events_for_their_topic():
    bus = EventBus()
    q1 = bus.subscribe("job:42")
    q2 = bus.subscribe("job:99")

    await bus.publish("job:42", {"item_id": 1, "status": "uploading"})
    await bus.publish("job:99", {"item_id": 7, "status": "prompting"})
    await bus.publish("job:42", {"item_id": 2, "status": "annotated"})

    e1 = await asyncio.wait_for(q1.get(), timeout=1)
    e2 = await asyncio.wait_for(q1.get(), timeout=1)
    e3 = await asyncio.wait_for(q2.get(), timeout=1)

    assert e1 == {"item_id": 1, "status": "uploading"}
    assert e2 == {"item_id": 2, "status": "annotated"}
    assert e3 == {"item_id": 7, "status": "prompting"}


@pytest.mark.asyncio
async def test_unsubscribe_removes_queue():
    bus = EventBus()
    q = bus.subscribe("topic")
    bus.unsubscribe("topic", q)
    await bus.publish("topic", {"x": 1})
    assert q.empty()


@pytest.mark.asyncio
async def test_publish_to_topic_with_no_subscribers_is_noop():
    bus = EventBus()
    await bus.publish("nobody", {"x": 1})
