import pytest

from backend.app.services.connection_monitor import ConnectionMonitor, ConnectionState


class FakeProvider:
    def __init__(self) -> None:
        self.healthy = True
        self.calls = 0

    async def health(self):
        self.calls += 1
        if not self.healthy:
            raise RuntimeError("unreachable")
        return {"ok": True}


@pytest.mark.asyncio
async def test_probe_once_records_transition_to_offline(db):
    p = FakeProvider()
    mon = ConnectionMonitor(provider=p, db_provider=lambda: db, interval_s=0.05)
    p.healthy = False
    state = await mon.probe_once()
    assert state == ConnectionState.offline

    cur = await db.execute("SELECT state, detail FROM connection_events ORDER BY id")
    rows = await cur.fetchall()
    assert any(r[0] == "offline" for r in rows)


@pytest.mark.asyncio
async def test_probe_once_records_transition_back_to_online(db):
    p = FakeProvider()
    mon = ConnectionMonitor(provider=p, db_provider=lambda: db, interval_s=0.05)
    p.healthy = False
    await mon.probe_once()
    p.healthy = True
    state = await mon.probe_once()
    assert state == ConnectionState.online

    cur = await db.execute("SELECT state FROM connection_events ORDER BY id")
    states = [r[0] for r in await cur.fetchall()]
    assert states[-1] == "online"


@pytest.mark.asyncio
async def test_manual_offline_pins_state(db):
    p = FakeProvider()
    mon = ConnectionMonitor(provider=p, db_provider=lambda: db, interval_s=0.05)
    mon.set_manual_offline(True)
    state = await mon.probe_once()
    assert state == ConnectionState.offline
    assert p.calls == 0  # probe is skipped while manual offline is set

    mon.set_manual_offline(False)
    state = await mon.probe_once()
    assert state == ConnectionState.online


@pytest.mark.asyncio
async def test_does_not_re_persist_unchanged_state(db):
    p = FakeProvider()
    mon = ConnectionMonitor(provider=p, db_provider=lambda: db, interval_s=0.05)
    # mon._state starts at online; first OK probe == no transition == no row.
    await mon.probe_once()
    cur = await db.execute("SELECT count(*) FROM connection_events")
    count = (await cur.fetchone())[0]
    assert count == 0
