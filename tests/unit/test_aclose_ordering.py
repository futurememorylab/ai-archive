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
