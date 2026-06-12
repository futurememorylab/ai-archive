"""VpnSupervisor — owns the onetun WireGuard subprocess on the cloud
deployment. Keeps onetun's actual state aligned with a persisted desired
(on/off) state, restarts it on crash, and reports status. Cloud-only:
constructed only when WireGuard is configured (Settings.vpn_managed).

Pure process + state. The CatDV-seat coupling on disable (log out over the
live tunnel, then drop the tunnel) is orchestrated by routes/vpn.py, not
here, so this class stays testable with an injected spawn function."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import NamedTuple, Protocol

logger = logging.getLogger(__name__)


class VpnStatus(NamedTuple):
    managed: bool
    desired: str            # "on" | "off"
    process_running: bool
    healthy: bool


class _Proc(Protocol):
    returncode: int | None

    def terminate(self) -> None: ...
    def kill(self) -> None: ...
    async def wait(self) -> int: ...


SpawnFn = Callable[[], Awaitable[_Proc]]
ProbeFn = Callable[[], Awaitable[bool]]
StrFn = Callable[[], Awaitable[str]]
SetFn = Callable[[str], Awaitable[None]]


class VpnSupervisor:
    def __init__(
        self,
        *,
        spawn: SpawnFn,
        get_desired: StrFn,
        set_desired: SetFn,
        probe_health: ProbeFn,
        restart_backoff_s: float = 2.0,
        kill_timeout_s: float = 5.0,
        health_interval_s: float = 15.0,
    ) -> None:
        self._spawn = spawn
        self._get_desired = get_desired
        self._set_desired = set_desired
        self._probe_health = probe_health
        self._backoff = restart_backoff_s
        self._kill_timeout = kill_timeout_s
        self._health_interval = health_interval_s
        self._proc: _Proc | None = None
        self._supervise_task: asyncio.Task[None] | None = None
        self._health_task: asyncio.Task[None] | None = None
        self._desired = "off"
        self._healthy = False
        self._stop = asyncio.Event()
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Lifespan startup: adopt the persisted desired state."""
        self._desired = await self._get_desired()
        if self._desired == "on":
            self._spin_up()

    async def enable(self) -> VpnStatus:
        async with self._lock:
            await self._set_desired("on")
            self._desired = "on"
            if self._supervise_task is None or self._supervise_task.done():
                self._spin_up()
            return self.status()

    async def disable(self) -> VpnStatus:
        async with self._lock:
            await self._set_desired("off")
            self._desired = "off"
            await self._spin_down()
            return self.status()

    def status(self) -> VpnStatus:
        running = self._proc is not None
        return VpnStatus(
            managed=True,
            desired=self._desired,
            process_running=running,
            healthy=self._healthy if running else False,
        )

    async def aclose(self) -> None:
        async with self._lock:
            await self._spin_down()

    # --- internals --------------------------------------------------

    def _spin_up(self) -> None:
        self._stop.clear()
        self._supervise_task = asyncio.create_task(self._supervise())
        self._health_task = asyncio.create_task(self._health_loop())

    async def _spin_down(self) -> None:
        self._stop.set()
        await self._kill_proc()          # terminate any live proc; _supervise also exits via the _stop flag
        for t in (self._supervise_task, self._health_task):
            if t is not None:
                try:
                    await t
                except asyncio.CancelledError:
                    pass
        self._supervise_task = None
        self._health_task = None
        self._healthy = False

    async def _supervise(self) -> None:
        while not self._stop.is_set():
            self._proc = await self._spawn()
            rc = await self._proc.wait()
            self._proc = None
            if self._stop.is_set():
                break
            logger.warning("onetun exited rc=%s; restarting in %ss", rc, self._backoff)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._backoff)
            except TimeoutError:
                pass

    async def _health_loop(self) -> None:
        while not self._stop.is_set():
            if self._proc is not None:
                try:
                    self._healthy = await self._probe_health()
                except Exception:  # noqa: BLE001 — probe is best-effort
                    self._healthy = False
            else:
                self._healthy = False
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._health_interval)
            except TimeoutError:
                pass

    async def _kill_proc(self) -> None:
        proc = self._proc
        if proc is None:
            return
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=self._kill_timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
        self._proc = None
