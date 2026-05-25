import asyncio

import backend.app.shutdown as shutdown_mod


def test_request_graceful_shutdown_sends_sigterm(monkeypatch):
    sent = []
    monkeypatch.setattr(shutdown_mod.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    shutdown_mod.request_graceful_shutdown()
    assert sent == [(shutdown_mod.os.getpid(), shutdown_mod.signal.SIGTERM)]


def test_schedule_graceful_shutdown_defers_via_loop():
    async def run():
        fired = []
        import backend.app.shutdown as m

        orig = m.request_graceful_shutdown
        m.request_graceful_shutdown = lambda: fired.append(True)
        try:
            m.schedule_graceful_shutdown(delay_s=0.01)
            assert fired == []  # deferred, not immediate
            await asyncio.sleep(0.05)
            assert fired == [True]  # fired after the delay
        finally:
            m.request_graceful_shutdown = orig

    asyncio.run(run())
