"""Unit tests for the helpers extracted from `context._build_archive_subsystem`."""

from __future__ import annotations

import asyncio

from backend.app.context import _is_online_provider, _startup_login
from backend.app.services.catdv_client import CatdvAuthError, CatdvBusyError
from backend.app.services.connection_monitor import ConnectionState


class _FakeCatdv:
    def __init__(self, exc: BaseException | None = None, delay_s: float = 0.0) -> None:
        self._exc = exc
        self._delay_s = delay_s
        self.closed = False

    async def login(self) -> None:
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
        if self._exc is not None:
            raise self._exc

    async def __aexit__(self, *args: object) -> None:
        self.closed = True


async def test_startup_login_ok_keeps_client_online():
    cat = _FakeCatdv()
    assert await _startup_login(cat, 1.0) == (cat, False)  # type: ignore[arg-type]


async def test_rejected_credentials_close_and_drop_the_client():
    cat = _FakeCatdv(CatdvAuthError("bad password"))
    assert await _startup_login(cat, 1.0) == (None, True)  # type: ignore[arg-type]
    assert cat.closed


async def test_busy_seat_and_timeout_keep_the_client_for_reconnect():
    busy = _FakeCatdv(CatdvBusyError("Maximum:2"))
    assert await _startup_login(busy, 1.0) == (busy, True)  # type: ignore[arg-type]
    slow = _FakeCatdv(delay_s=1.0)
    assert await _startup_login(slow, 0.01) == (slow, True)  # type: ignore[arg-type]
    assert not busy.closed and not slow.closed


class _Monitor:
    def __init__(self, state: ConnectionState) -> None:
        self.state = state

    def current_state(self) -> ConnectionState:
        return self.state


def test_is_online_follows_the_monitor_unless_forced_offline():
    holder: dict = {"monitor": None}
    is_online = _is_online_provider(False, holder)
    assert is_online()  # no monitor yet: assume online
    holder["monitor"] = _Monitor(ConnectionState.offline)
    assert not is_online()
    holder["monitor"].state = ConnectionState.online
    assert is_online()
    assert not _is_online_provider(True, holder)()
