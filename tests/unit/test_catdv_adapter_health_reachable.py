# tests/unit/test_catdv_adapter_health_reachable.py
"""adapter.health() reports `reachable` so the monitor can tell
'tunnel up, logged out' (reachable) from 'tunnel down' (raised). See spec."""

import pytest

from backend.app.archive.providers.catdv.adapter import CatdvArchiveAdapter
from backend.app.services.catdv_client import (
    CatdvAuthError,
    CatdvBusyError,
    CatdvError,
)


class _Client:
    def __init__(self, exc):
        self._exc = exc

    async def health(self):
        if self._exc is not None:
            raise self._exc
        return {}


def _provider(client):
    p = CatdvArchiveAdapter.__new__(CatdvArchiveAdapter)
    p._client = client
    p._is_online_provider = lambda: True
    return p


@pytest.mark.asyncio
async def test_auth_envelope_is_reachable():
    h = await _provider(_Client(CatdvAuthError("no session"))).health()
    assert h.ok is False and h.reachable is True


@pytest.mark.asyncio
async def test_busy_is_reachable():
    h = await _provider(_Client(CatdvBusyError("max sessions"))).health()
    assert h.ok is False and h.reachable is True


@pytest.mark.asyncio
async def test_generic_error_is_not_reachable():
    h = await _provider(_Client(CatdvError("bad base url"))).health()
    assert h.ok is False and h.reachable is False


@pytest.mark.asyncio
async def test_absent_client_is_not_reachable():
    h = await _provider(None).health()
    assert h.ok is False and h.reachable is False


@pytest.mark.asyncio
async def test_ok_is_reachable():
    h = await _provider(_Client(None)).health()
    assert h.ok is True and h.reachable is True
