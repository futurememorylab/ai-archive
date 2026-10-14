"""Active catalog resolution (#124): cookie → catalog id, online gate."""

from types import SimpleNamespace

import pytest
from starlette.requests import Request

from backend.app.services.active_catalog import COOKIE, active_catalog_id, is_live_online
from backend.app.services.connection_monitor import ConnectionState

SETTINGS = SimpleNamespace(catdv_catalog_id=881507)


def _req(cookie: str | None) -> Request:
    # latin-1: how Starlette decodes header bytes, so "\u00b2" arrives as "²".
    headers = [] if cookie is None else [(b"cookie", f"{COOKIE}={cookie}".encode("latin-1"))]
    return Request({"type": "http", "headers": headers})


def test_cookie_wins():
    assert active_catalog_id(_req("913722"), SETTINGS) == "913722"


def test_missing_cookie_defaults():
    assert active_catalog_id(_req(None), SETTINGS) == "881507"


@pytest.mark.parametrize("junk", ["abc", "", "12x", "-1", "\u00b2", "9" * 500])
def test_non_numeric_cookie_defaults(junk):
    assert active_catalog_id(_req(junk), SETTINGS) == "881507"


class _Mon:
    def __init__(self, state, forced=False):
        self._state, self.is_forced = state, forced

    def current_state(self):
        return self._state


def test_is_live_online():
    assert is_live_online(None) is False
    assert is_live_online(SimpleNamespace(connection_monitor=_Mon(ConnectionState.online, forced=True))) is False
    assert is_live_online(SimpleNamespace(connection_monitor=_Mon(ConnectionState.offline))) is False
    assert is_live_online(SimpleNamespace(connection_monitor=_Mon(ConnectionState.online))) is True
