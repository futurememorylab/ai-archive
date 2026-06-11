import pytest

from backend.app.context import CoreCtx, LiveCtx


@pytest.mark.asyncio
async def test_aclose_stops_monitor_before_logout():
    calls: list[str] = []

    class RecStop:
        def __init__(self, name: str) -> None:
            self.name = name

        async def stop(self) -> None:
            calls.append(f"{self.name}.stop")

    class FakeCatdv:
        async def __aexit__(self, *exc_info) -> None:
            calls.append("catdv.logout")

    class FakeDbCm:
        async def __aexit__(self, *exc_info) -> None:
            calls.append("db.close")

    core = CoreCtx(settings=object(), db=object(), db_cm=FakeDbCm())  # type: ignore[arg-type]
    live = LiveCtx(
        core=core,
        archive=object(),  # type: ignore[arg-type]
        ai_store=object(),  # type: ignore[arg-type]
        gemini=object(),  # type: ignore[arg-type]
        sync_engine=RecStop("sync"),  # type: ignore[arg-type]
        connection_monitor=RecStop("monitor"),  # type: ignore[arg-type]
        workspace_manager=object(),  # type: ignore[arg-type]
        lru_eviction=RecStop("lru"),  # type: ignore[arg-type]
        _gcs_service=object(),  # type: ignore[arg-type]
        catdv=FakeCatdv(),  # type: ignore[arg-type]
    )

    await live.aclose()

    assert "monitor.stop" in calls and "catdv.logout" in calls
    assert calls.index("monitor.stop") < calls.index("catdv.logout")
    assert calls.index("catdv.logout") < calls.index("db.close")


@pytest.mark.asyncio
async def test_aclose_vpn_supervisor_after_catdv_logout():
    """vpn_supervisor must be torn down AFTER catdv.__aexit__ so the
    DELETE /session logout travels over a live tunnel and the seat is
    released (not lost to a dead-tunnel timeout)."""
    calls: list[str] = []

    class RecStop:
        def __init__(self, name: str) -> None:
            self.name = name

        async def stop(self) -> None:
            calls.append(f"{self.name}.stop")

    class FakeCatdv:
        async def __aexit__(self, *exc_info) -> None:
            calls.append("catdv.logout")

    class FakeVpnSupervisor:
        async def aclose(self) -> None:
            calls.append("vpn.aclose")

    class FakeDbCm:
        async def __aexit__(self, *exc_info) -> None:
            calls.append("db.close")

    core = CoreCtx(settings=object(), db=object(), db_cm=FakeDbCm())  # type: ignore[arg-type]
    live = LiveCtx(
        core=core,
        archive=object(),  # type: ignore[arg-type]
        ai_store=object(),  # type: ignore[arg-type]
        gemini=object(),  # type: ignore[arg-type]
        sync_engine=RecStop("sync"),  # type: ignore[arg-type]
        connection_monitor=RecStop("monitor"),  # type: ignore[arg-type]
        workspace_manager=object(),  # type: ignore[arg-type]
        lru_eviction=RecStop("lru"),  # type: ignore[arg-type]
        _gcs_service=object(),  # type: ignore[arg-type]
        catdv=FakeCatdv(),  # type: ignore[arg-type]
        vpn_supervisor=FakeVpnSupervisor(),  # type: ignore[arg-type]
    )

    await live.aclose()

    assert "catdv.logout" in calls and "vpn.aclose" in calls
    # The tunnel must outlive the logout so the seat is released over a live
    # connection.
    assert calls.index("catdv.logout") < calls.index("vpn.aclose")
    assert calls.index("vpn.aclose") < calls.index("db.close")
