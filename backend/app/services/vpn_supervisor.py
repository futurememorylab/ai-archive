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
    # True while the tunnel is coming up (desired on, health not yet confirmed
    # and still within the connect grace window) — the UI shows an amber
    # "Connecting…" instead of a red "Unreachable" during the handshake. Falls
    # through to unreachable (connecting=False, healthy=False) once the grace
    # window of failed probes is exhausted. Trailing default keeps existing
    # VpnStatus(...) call sites (and test stubs) valid.
    connecting: bool = False


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
        # Bounded so the SIGTERM teardown fits Cloud Run's 10s grace and
        # leaves room for Litestream's final WAL sync. onetun has no state to
        # preserve (the seat-release DELETE already went out first).
        kill_timeout_s: float = 2.0,
        health_interval_s: float = 15.0,
        # While the tunnel is coming up (not yet healthy) we probe on this
        # shorter cadence so "Connecting…" resolves to "Connected" in a few
        # seconds rather than waiting a full health_interval. Once healthy we
        # settle back to health_interval_s.
        connect_interval_s: float = 3.0,
        # How many consecutive failed probes to tolerate as "Connecting…"
        # before declaring the tunnel Unreachable. Covers the WireGuard
        # handshake window; after this the row turns red and offers Retry.
        connect_grace: int = 3,
    ) -> None:
        self._spawn = spawn
        self._get_desired = get_desired
        self._set_desired = set_desired
        self._probe_health = probe_health
        self._backoff = restart_backoff_s
        self._kill_timeout = kill_timeout_s
        self._health_interval = health_interval_s
        self._connect_interval = connect_interval_s
        self._connect_grace = connect_grace
        self._proc: _Proc | None = None
        self._supervise_task: asyncio.Task[None] | None = None
        self._health_task: asyncio.Task[None] | None = None
        self._desired = "off"
        self._healthy = False
        self._ever_healthy = False   # healthy at least once since this spin-up
        self._connect_fails = 0      # consecutive failed probes while connecting
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

    async def probe_now(self) -> VpnStatus:
        """Force an immediate health re-probe (user-driven 'Retry').

        The health loop already re-probes every ``health_interval_s`` and
        ``_supervise`` auto-respawns a dead proc; this exposes the same probe
        on demand so the UI Retry isn't a no-op. Does NOT bounce the tunnel —
        a wedged proc is auto-respawned; a deliberate fresh tunnel is
        disable()+enable(). Best-effort, mirroring ``_health_loop``.
        """
        async with self._lock:
            if self._proc is not None:
                try:
                    ok = await self._probe_health()
                except Exception:  # noqa: BLE001 — probe is best-effort
                    ok = False
                self._record_probe(ok)
            else:
                self._healthy = False
            return self.status()

    def status(self) -> VpnStatus:
        running = self._proc is not None
        healthy = self._healthy if running else False
        # Connecting: wanted on, not yet confirmed healthy (proc still spinning
        # up or mid-handshake), and we haven't exhausted the grace window. Once
        # it has ever been healthy, a later drop is a real failure (Unreachable),
        # not a connect — so _ever_healthy gates this off.
        connecting = (
            self._desired == "on"
            and not healthy
            and not self._ever_healthy
            and self._connect_fails < self._connect_grace
        )
        return VpnStatus(
            managed=True,
            desired=self._desired,
            process_running=running,
            healthy=healthy,
            connecting=connecting,
        )

    async def aclose(self) -> None:
        async with self._lock:
            await self._spin_down()

    # --- internals --------------------------------------------------

    def _spin_up(self) -> None:
        self._stop.clear()
        self._healthy = False
        self._ever_healthy = False    # fresh tunnel → re-enter the connecting phase
        self._connect_fails = 0
        self._supervise_task = asyncio.create_task(self._supervise())
        self._health_task = asyncio.create_task(self._health_loop())

    def _record_probe(self, ok: bool) -> None:
        """Fold one probe result into health + connect-phase counters."""
        self._healthy = ok
        if ok:
            self._ever_healthy = True
            self._connect_fails = 0
        elif not self._ever_healthy:
            self._connect_fails += 1

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
                    ok = await self._probe_health()
                except Exception:  # noqa: BLE001 — probe is best-effort
                    ok = False
                self._record_probe(ok)
            else:
                self._healthy = False
            # Probe fast while still coming up so "Connecting…" resolves
            # quickly; settle to the normal interval once healthy.
            interval = self._health_interval if self._ever_healthy else self._connect_interval
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
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
