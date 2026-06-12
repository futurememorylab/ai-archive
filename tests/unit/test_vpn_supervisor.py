import asyncio

from backend.app.services.vpn_supervisor import VpnSupervisor


class FakeProc:
    def __init__(self):
        self.returncode = None
        self._exit = asyncio.Event()
        self.terminated = False
        self.killed = False

    def terminate(self):
        self.terminated = True
        self.returncode = -15
        self._exit.set()

    def kill(self):
        self.killed = True
        self.returncode = -9
        self._exit.set()

    async def wait(self):
        await self._exit.wait()
        return self.returncode or 0


def _make(desired="off", spawned=None, probe_ok=True):
    state = {"desired": desired}
    spawned = spawned if spawned is not None else []

    async def spawn():
        p = FakeProc()
        spawned.append(p)
        return p

    async def get_desired():
        return state["desired"]

    async def set_desired(v):
        state["desired"] = v

    async def probe_health():
        return probe_ok

    sup = VpnSupervisor(
        spawn=spawn, get_desired=get_desired, set_desired=set_desired,
        probe_health=probe_health, restart_backoff_s=0.01,
        kill_timeout_s=0.2, health_interval_s=0.01,
    )
    return sup, state, spawned


async def test_start_off_does_not_spawn():
    sup, state, spawned = _make(desired="off")
    await sup.start()
    await asyncio.sleep(0.02)
    assert spawned == []
    assert sup.status().process_running is False
    assert sup.status().desired == "off"
    await sup.aclose()


async def test_start_on_spawns():
    sup, state, spawned = _make(desired="on")
    await sup.start()
    await asyncio.sleep(0.02)
    assert len(spawned) == 1
    assert sup.status().process_running is True
    await sup.aclose()


async def test_enable_persists_and_spawns():
    sup, state, spawned = _make(desired="off")
    await sup.start()
    st = await sup.enable()
    await asyncio.sleep(0.02)
    assert state["desired"] == "on"
    assert st.desired == "on"
    assert sup.status().process_running is True
    await sup.aclose()


async def test_disable_persists_and_kills():
    sup, state, spawned = _make(desired="on")
    await sup.start()
    await asyncio.sleep(0.02)
    st = await sup.disable()
    assert state["desired"] == "off"
    assert st.process_running is False
    assert spawned[0].terminated is True
    await sup.aclose()


async def test_restart_on_crash_while_on():
    sup, state, spawned = _make(desired="on")
    await sup.start()
    await asyncio.sleep(0.02)
    spawned[0].returncode = 1
    spawned[0]._exit.set()          # simulate onetun crashing
    await asyncio.sleep(0.05)       # backoff is 0.01s
    assert len(spawned) >= 2        # respawned
    await sup.aclose()


async def test_no_restart_after_disable():
    sup, state, spawned = _make(desired="on")
    await sup.start()
    await asyncio.sleep(0.02)
    await sup.disable()
    n = len(spawned)
    await asyncio.sleep(0.05)
    assert len(spawned) == n        # stayed down
    await sup.aclose()


async def test_healthy_reflects_probe():
    sup, state, spawned = _make(desired="on", probe_ok=True)
    await sup.start()
    await asyncio.sleep(0.03)
    assert sup.status().healthy is True
    await sup.aclose()
    assert sup.status().healthy is False


async def test_enable_disable_enable_roundtrip():
    """Enable → disable → enable should work cleanly with a fresh proc each time."""
    sup, state, spawned = _make(desired="off")
    await sup.start()

    # First enable
    await sup.enable()
    await asyncio.sleep(0.02)
    assert sup.status().process_running is True
    assert sup.status().desired == "on"
    first_proc_count = len(spawned)
    assert first_proc_count >= 1

    # Disable
    await sup.disable()
    assert sup.status().process_running is False
    assert sup.status().desired == "off"

    # Second enable — must spawn a fresh proc
    await sup.enable()
    await asyncio.sleep(0.02)
    assert sup.status().process_running is True
    assert sup.status().desired == "on"
    assert len(spawned) > first_proc_count  # a fresh proc was spawned

    await sup.aclose()
    assert sup.status().process_running is False
