"""IdleDisconnector logs out (freeing the seat) and re-probes once the
client has been idle past the threshold; activity within it is a no-op."""

import pytest

from backend.app.services.idle_disconnector import IdleDisconnector


class FakeClient:
    def __init__(self, *, logged_in, last_activity):
        self._logged_in = logged_in
        self._last_activity = last_activity
        self.logout_calls = 0

    @property
    def logged_in(self):
        return self._logged_in

    @property
    def last_activity(self):
        return self._last_activity

    async def logout(self):
        self.logout_calls += 1
        self._logged_in = False


class FakeMonitor:
    def __init__(self):
        self.probes = 0

    async def probe_once(self):
        self.probes += 1


def _now(value):
    return lambda: value


@pytest.mark.asyncio
async def test_idle_past_threshold_disconnects():
    client = FakeClient(logged_in=True, last_activity=0.0)
    monitor = FakeMonitor()
    idle = IdleDisconnector(
        client=client, monitor=monitor, idle_timeout_s=900, clock=_now(901.0)
    )
    assert await idle.check_once() is True
    assert client.logout_calls == 1
    assert monitor.probes == 1


@pytest.mark.asyncio
async def test_recent_activity_is_noop():
    client = FakeClient(logged_in=True, last_activity=500.0)
    monitor = FakeMonitor()
    idle = IdleDisconnector(
        client=client, monitor=monitor, idle_timeout_s=900, clock=_now(900.0)
    )
    assert await idle.check_once() is False
    assert client.logout_calls == 0


@pytest.mark.asyncio
async def test_not_logged_in_is_noop():
    client = FakeClient(logged_in=False, last_activity=0.0)
    monitor = FakeMonitor()
    idle = IdleDisconnector(
        client=client, monitor=monitor, idle_timeout_s=1, clock=_now(9999.0)
    )
    assert await idle.check_once() is False
    assert client.logout_calls == 0
