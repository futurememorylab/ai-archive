import asyncio
from collections import defaultdict
from typing import Any


class EventBus:
    """Minimal in-process pub/sub for SSE. One queue per subscriber per topic."""

    def __init__(self) -> None:
        self._subs: dict[str, list[asyncio.Queue]] = defaultdict(list)
        self._lock = asyncio.Lock()

    def subscribe(self, topic: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1024)
        self._subs[topic].append(q)
        return q

    def unsubscribe(self, topic: str, q: asyncio.Queue) -> None:
        if topic in self._subs and q in self._subs[topic]:
            self._subs[topic].remove(q)
            if not self._subs[topic]:
                del self._subs[topic]

    async def publish(self, topic: str, payload: dict[str, Any]) -> None:
        for q in list(self._subs.get(topic, [])):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                q.put_nowait(payload)
