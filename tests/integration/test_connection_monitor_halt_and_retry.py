import asyncio

import pytest

from backend.app.services.connection_monitor import (
    ConnectionMonitor,
    ConnectionState,
)
from backend.app.services.events import EventBus


class StubProvider:
    def __init__(self, *, healthy: bool):
        self.healthy = healthy
        self.calls = 0

    async def health(self):
        self.calls += 1
        if not self.healthy:
            raise RuntimeError("offline")
        return None


@pytest.mark.asyncio
async def test_loop_halts_on_failure(db):
    provider = StubProvider(healthy=False)
    monitor = ConnectionMonitor(
        provider=provider,
        db_provider=lambda: db,
        interval_s=0.05,
        timeout_s=0.5,
        event_bus=EventBus(),
    )
    await monitor.probe_once()  # explicit initial probe
    assert monitor.current_state() == ConnectionState.offline

    await monitor.start()
    await asyncio.sleep(0.3)
    await monitor.stop()
    # one probe at start of loop; loop must halt after seeing offline
    assert provider.calls <= 2


@pytest.mark.asyncio
async def test_retry_now_success_flips_online_and_restarts_loop(db):
    provider = StubProvider(healthy=False)
    monitor = ConnectionMonitor(
        provider=provider,
        db_provider=lambda: db,
        interval_s=0.05,
        timeout_s=0.5,
        event_bus=EventBus(),
    )
    await monitor.probe_once()
    assert monitor.current_state() == ConnectionState.offline

    provider.healthy = True
    result = await monitor.retry_now()
    assert result == ConnectionState.online
    assert monitor.current_state() == ConnectionState.online
    await monitor.stop()


@pytest.mark.asyncio
async def test_retry_now_failure_stays_offline(db):
    provider = StubProvider(healthy=False)
    monitor = ConnectionMonitor(
        provider=provider,
        db_provider=lambda: db,
        interval_s=0.05,
        timeout_s=0.5,
        event_bus=EventBus(),
    )
    await monitor.probe_once()
    result = await monitor.retry_now()
    assert result == ConnectionState.offline
    assert monitor.current_state() == ConnectionState.offline


@pytest.mark.asyncio
async def test_forced_offline_ignores_probes(db):
    provider = StubProvider(healthy=True)
    monitor = ConnectionMonitor(
        provider=provider,
        db_provider=lambda: db,
        interval_s=0.05,
        timeout_s=0.5,
        event_bus=EventBus(),
        forced_offline=True,
    )
    await monitor.probe_once()
    assert monitor.current_state() == ConnectionState.offline
    assert provider.calls == 0

    result = await monitor.retry_now()
    assert result == ConnectionState.offline
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_mid_session_failure_halts_loop(db):
    provider = StubProvider(healthy=True)
    monitor = ConnectionMonitor(
        provider=provider,
        db_provider=lambda: db,
        interval_s=0.05,
        timeout_s=0.5,
        event_bus=EventBus(),
    )
    await monitor.probe_once()
    assert monitor.current_state() == ConnectionState.online
    await monitor.start()
    await asyncio.sleep(0.1)
    provider.healthy = False
    await asyncio.sleep(0.3)
    await monitor.stop()
    assert monitor.current_state() == ConnectionState.offline
    calls_when_offline = provider.calls
    await asyncio.sleep(0.2)
    assert provider.calls == calls_when_offline
