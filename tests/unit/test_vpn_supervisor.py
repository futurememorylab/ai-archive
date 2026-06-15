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


def _make(desired="off", spawned=None, probe_ok=True, probe=None, **kw):
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
        probe_health=probe if probe is not None else probe_health,
        restart_backoff_s=0.01, kill_timeout_s=0.2, health_interval_s=0.01,
        **kw,
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


def test_default_kill_timeout_fits_grace():
    # 2s onetun kill + 2s CatDV logout (see catdv_client.LOGOUT_TIMEOUT_S)
    # leaves ~6s of Cloud Run's 10s SIGTERM grace for Litestream's final sync.
    import inspect

    from backend.app.services.vpn_supervisor import VpnSupervisor

    default = inspect.signature(VpnSupervisor).parameters["kill_timeout_s"].default
    assert default == 2.0


async def test_probe_now_running_sets_healthy_from_probe():
    sup, state, spawned = _make(desired="on", probe_ok=True)
    await sup.start()
    await asyncio.sleep(0.02)
    st = await sup.probe_now()
    assert st.healthy is True
    assert sup.status().healthy is True
    await sup.aclose()


async def test_probe_now_running_probe_false_marks_unhealthy():
    sup, state, spawned = _make(desired="on", probe_ok=False)
    await sup.start()
    await asyncio.sleep(0.02)
    st = await sup.probe_now()
    assert st.healthy is False
    assert st.process_running is True   # proc still up; only the probe failed
    await sup.aclose()


async def test_probe_now_not_running_is_unhealthy_noop():
    sup, state, spawned = _make(desired="off")
    await sup.start()
    st = await sup.probe_now()
    assert st.process_running is False
    assert st.healthy is False
    await sup.aclose()


async def test_probe_now_swallows_probe_exception():
    sup, state, spawned = _make(desired="on")
    await sup.start()
    await asyncio.sleep(0.02)

    async def boom():
        raise RuntimeError("probe blew up")

    sup._probe_health = boom            # same best-effort contract as _health_loop
    st = await sup.probe_now()
    assert st.healthy is False
    await sup.aclose()


# --- connecting phase (amber transition, not red Unreachable) -------------

def test_status_connecting_phase_state_machine():
    """White-box: connecting is True while coming up, falls through to
    unreachable after the grace window, and is gated off once ever-healthy."""
    sup, _, _ = _make(desired="off", connect_grace=3)
    # pretend spun up and running, no probe result yet
    sup._desired = "on"
    sup._proc = object()
    sup._healthy = False
    sup._ever_healthy = False
    sup._connect_fails = 0
    assert sup.status().connecting is True            # before any probe
    sup._record_probe(False)                          # handshake not ready
    assert sup.status().connecting is True            # still within grace
    sup._record_probe(False)
    sup._record_probe(False)                           # now 3 fails == grace
    assert sup.status().connecting is False            # → Unreachable
    assert sup.status().healthy is False
    sup._record_probe(True)                            # comes up
    assert sup.status().healthy is True
    assert sup.status().connecting is False            # Connected, not connecting
    sup._record_probe(False)                           # later drop
    assert sup.status().connecting is False            # Unreachable (ever_healthy gate)


async def test_enable_reports_connecting_immediately():
    # Right after enable() the proc hasn't spawned and no probe has run, so the
    # status must read Connecting (amber), never Unreachable (red).
    sup, _, _ = _make(desired="off", probe_ok=False, connect_grace=5)
    await sup.start()
    st = await sup.enable()
    assert st.connecting is True
    assert st.healthy is False
    assert st.desired == "on"
    await sup.aclose()


async def test_connecting_resolves_to_connected():
    calls = {"n": 0}

    async def probe():
        calls["n"] += 1
        return calls["n"] >= 2          # first probe fails (handshake), then up

    sup, _, _ = _make(desired="off", probe=probe, connect_grace=5, connect_interval_s=0.01)
    await sup.start()
    await sup.enable()
    assert sup.status().connecting is True            # immediately
    await asyncio.sleep(0.05)                          # let the fast probes run
    st = sup.status()
    assert st.healthy is True
    assert st.connecting is False
    await sup.aclose()


async def test_connecting_exhausts_grace_to_unreachable():
    sup, _, _ = _make(desired="off", probe_ok=False, connect_grace=2, connect_interval_s=0.01)
    await sup.start()
    await sup.enable()
    await asyncio.sleep(0.08)                          # several failed probes
    st = sup.status()
    assert st.connecting is False                      # grace exhausted
    assert st.healthy is False                         # Unreachable
    await sup.aclose()
