"""Releases the CatDV seat after a period of operator inactivity.

The Cloud Run instance is always-on, so a forgotten Connect would hold a
seat indefinitely. This task logs out (DELETE /session) and re-probes the
monitor (→ "disconnected") once last_activity is older than the threshold.
Activity is operator-driven CatDV calls only; the health probe and the
pill poll do not reset it (see CatdvClient.last_activity)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any


class IdleDisconnector:
    def __init__(
        self,
        *,
        client: Any,
        monitor: Any,
        idle_timeout_s: float,
        check_interval_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._monitor = monitor
        self._idle = float(idle_timeout_s)
        self._interval = float(check_interval_s)
        self._clock = clock
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def check_once(self) -> bool:
        """One idle check. Returns True iff it disconnected this call."""
        if not self._client.logged_in:
            return False
        if self._clock() - self._client.last_activity <= self._idle:
            return False
        await self._client.logout()
        await self._monitor.probe_once()
        return True

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=2.0)
        except TimeoutError:
            self._task.cancel()
        finally:
            self._task = None

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.check_once()
            except Exception:  # noqa: BLE001 — watchdog loop must not die
                pass
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except TimeoutError:
                pass
