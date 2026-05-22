"""ConnectionMonitor: periodic provider.health() probe + state machine.

Transitions are persisted to `connection_events` and broadcast on the
`EventBus` topic `"connection"` so the (PR 5) UI pill can subscribe.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import aiosqlite


class ConnectionState(StrEnum):
    online = "online"
    degraded = "degraded"
    offline = "offline"
    syncing = "syncing"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class ConnectionMonitor:
    def __init__(
        self,
        *,
        provider: Any,
        db_provider: Callable[[], aiosqlite.Connection],
        interval_s: float = 30.0,
        timeout_s: float = 5.0,
        event_bus: Any = None,
        clock: Callable[[], datetime] | None = None,
        forced_offline: bool = False,
        initial_state: ConnectionState = ConnectionState.online,
    ) -> None:
        self._provider = provider
        self._db_provider = db_provider
        self._interval_s = interval_s
        self._timeout_s = timeout_s
        self._event_bus = event_bus
        self._clock = clock or (lambda: datetime.now(UTC))
        self._state: ConnectionState = initial_state
        self._manual_offline: bool = False
        self._forced_offline: bool = forced_offline
        if forced_offline:
            self._state = ConnectionState.offline
        self._task: asyncio.Task | None = None
        self._stop_evt: asyncio.Event = asyncio.Event()

    @property
    def is_forced(self) -> bool:
        return self._forced_offline

    def current_state(self) -> ConnectionState:
        if self._forced_offline or self._manual_offline:
            return ConnectionState.offline
        return self._state

    def set_manual_offline(self, enabled: bool) -> None:
        was_manual = self._manual_offline
        self._manual_offline = enabled
        if was_manual != enabled:
            target = ConnectionState.offline if enabled else self._state
            detail = "manual offline" if enabled else "manual offline cleared"
            # fire-and-forget; persist is awaitable but ok to schedule.
            try:
                asyncio.get_running_loop().create_task(
                    self._persist_and_publish(target, detail)
                )
            except RuntimeError:
                # no running loop (rare; e.g. setup-time); skip.
                pass

    async def probe_once(self) -> ConnectionState:
        """One health probe; update state if it changed; return current."""
        if self._forced_offline or self._manual_offline:
            return ConnectionState.offline
        new_state: ConnectionState
        detail: str | None = None
        try:
            health = await asyncio.wait_for(
                self._provider.health(), timeout=self._timeout_s
            )
        except TimeoutError:
            new_state = ConnectionState.offline
            detail = "health probe timeout"
        except Exception as exc:  # noqa: BLE001 — provider error surface
            new_state = ConnectionState.offline
            detail = f"{type(exc).__name__}: {exc}"
        else:
            ok = getattr(health, "ok", True) if health is not None else True
            if ok:
                new_state = ConnectionState.online
            else:
                new_state = ConnectionState.offline
                detail = getattr(health, "detail", None) or "health probe not ok"
        if new_state != self._state:
            old = self._state
            self._state = new_state
            await self._persist_and_publish(new_state, detail or f"{old} → {new_state}")
        return self.current_state()

    async def _persist_and_publish(
        self, state: ConnectionState, detail: str | None
    ) -> None:
        conn = self._db_provider()
        await conn.execute(
            "INSERT INTO connection_events (state, detail, at) VALUES (?, ?, ?)",
            (str(state.value), detail, _now_iso()),
        )
        await conn.commit()
        if self._event_bus is not None:
            await self._event_bus.publish(
                "connection",
                {"state": str(state.value), "detail": detail, "at": _now_iso()},
            )

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_evt.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_evt.set()
        try:
            await asyncio.wait_for(self._task, timeout=2.0)
        except TimeoutError:
            self._task.cancel()
        finally:
            self._task = None

    async def retry_now(self) -> ConnectionState:
        """User-triggered probe. On success, resumes the probe loop."""
        if self._forced_offline:
            return ConnectionState.offline
        state = await self.probe_once()
        if state == ConnectionState.online and self._task is None:
            await self.start()
        return state

    async def _loop(self) -> None:
        while not self._stop_evt.is_set():
            try:
                state = await self.probe_once()
            except Exception:  # noqa: BLE001 — loop must not die
                state = ConnectionState.offline
            if state != ConnectionState.online:
                # halt — user must explicitly retry_now() to resume
                return
            try:
                await asyncio.wait_for(
                    self._stop_evt.wait(), timeout=self._interval_s
                )
            except TimeoutError:
                pass
