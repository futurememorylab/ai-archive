import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.routes.connection import router as connection_router
from backend.app.services.connection_monitor import ConnectionState


class StubMonitor:
    def __init__(self, *, forced: bool = False, state: str = "online"):
        self._forced = forced
        self._state = state

    @property
    def is_forced(self) -> bool:
        return self._forced

    def current_state(self) -> ConnectionState:
        return ConnectionState(self._state)

    async def retry_now(self) -> ConnectionState:
        if self._forced:
            return ConnectionState.offline
        return ConnectionState(self._state)


def _build_app(monitor: StubMonitor) -> FastAPI:
    app = FastAPI()
    app.include_router(connection_router)

    @app.get("/api/health")
    async def health():
        if monitor.is_forced:
            mode = "forced_offline"
        elif monitor.current_state() == ConnectionState.online:
            mode = "online"
        else:
            mode = "offline"
        return {"ok": True, "mode": mode}

    class Ctx:
        connection_monitor = monitor
        event_bus = None

    ctx = Ctx()
    app.state.core_ctx = ctx
    app.state.live_ctx = ctx
    return app


@pytest.fixture
def client_offline():
    return TestClient(_build_app(StubMonitor(state="offline")))


@pytest.fixture
def client_online():
    return TestClient(_build_app(StubMonitor(state="online")))


@pytest.fixture
def client_forced_offline():
    return TestClient(_build_app(StubMonitor(forced=True, state="offline")))


def test_retry_endpoint_returns_state(client_offline):
    resp = client_offline.post("/api/connection/retry")
    assert resp.status_code in (200, 409)
    body = resp.json()
    assert "state" in body


def test_retry_endpoint_409_when_forced(client_forced_offline):
    resp = client_forced_offline.post("/api/connection/retry")
    assert resp.status_code == 409
    assert resp.json().get("detail", "").startswith("forced")


def test_state_endpoint_includes_mode(client_online):
    resp = client_online.get("/api/connection/state")
    body = resp.json()
    assert body["state"] in {"online", "offline", "degraded", "syncing"}
    assert body["mode"] in {"online", "offline", "forced_offline"}


def test_state_endpoint_mode_forced(client_forced_offline):
    resp = client_forced_offline.get("/api/connection/state")
    body = resp.json()
    assert body["mode"] == "forced_offline"


def test_health_endpoint_includes_mode(client_online):
    resp = client_online.get("/api/health")
    body = resp.json()
    assert body["mode"] in {"online", "offline", "forced_offline"}
