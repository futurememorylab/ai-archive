# tests/integration/test_connection_monitor_manual.py
"""Manual-mode monitor: seat truth from logged_in(), reachability from the
probe; the loop keeps probing (no halt) so the indicator tracks the tunnel."""

import asyncio

import pytest

from backend.app.services.connection_monitor import (
    ConnectionMonitor,
    ConnectionState,
)
from backend.app.services.events import EventBus


class _Health:
    def __init__(self, ok, reachable):
        self.ok = ok
        self.reachable = reachable


class StubProvider:
    """health() returns a _Health, or raises to simulate an unreachable tunnel."""

    def __init__(self, *, ok=True, reachable=True, raises=False):
        self.ok = ok
        self.reachable = reachable
        self.raises = raises
        self.calls = 0

    async def health(self):
        self.calls += 1
        if self.raises:
            raise RuntimeError("connect error")
        return _Health(self.ok, self.reachable)


def _monitor(db, provider, *, logged_in):
    return ConnectionMonitor(
        provider=provider,
        db_provider=lambda: db,
        interval_s=0.05,
        timeout_s=0.5,
        event_bus=EventBus(),
        manual=True,
        logged_in=lambda: logged_in[0],
        initial_state=ConnectionState.disconnected,
    )


@pytest.mark.asyncio
async def test_logged_in_and_ok_is_online(db):
    logged_in = [True]
    m = _monitor(db, StubProvider(ok=True), logged_in=logged_in)
    assert await m.probe_once() == ConnectionState.online


@pytest.mark.asyncio
async def test_reachable_but_logged_out_is_disconnected(db):
    logged_in = [False]
    # ok=True from a public /api/info must NOT read as online when logged out
    m = _monitor(db, StubProvider(ok=True, reachable=True), logged_in=logged_in)
    assert await m.probe_once() == ConnectionState.disconnected


@pytest.mark.asyncio
async def test_unreachable_is_offline(db):
    logged_in = [False]
    m = _monitor(db, StubProvider(raises=True), logged_in=logged_in)
    assert await m.probe_once() == ConnectionState.offline


@pytest.mark.asyncio
async def test_manual_loop_does_not_halt_on_non_online(db):
    logged_in = [False]
    provider = StubProvider(ok=False, reachable=True)  # disconnected
    m = _monitor(db, provider, logged_in=logged_in)
    await m.start()
    await asyncio.sleep(0.25)
    await m.stop()
    assert provider.calls >= 3  # kept probing despite being non-online
