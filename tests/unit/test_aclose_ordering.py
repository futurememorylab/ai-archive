import pytest

from backend.app.context import AppContext


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

    ctx = AppContext(settings=object(), db=object(), db_cm=FakeDbCm())  # type: ignore[arg-type]
    ctx.connection_monitor = RecStop("monitor")  # type: ignore[assignment]
    ctx.sync_engine = RecStop("sync")  # type: ignore[assignment]
    ctx.catdv = FakeCatdv()  # type: ignore[assignment]

    await ctx.aclose()

    assert "monitor.stop" in calls and "catdv.logout" in calls
    assert calls.index("monitor.stop") < calls.index("catdv.logout")
    assert calls.index("catdv.logout") < calls.index("db.close")
