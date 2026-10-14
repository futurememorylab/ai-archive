"""Unit tests for the live CatDV writeback probe (#123, Task 1).

The probe spends the scarce CatDV license seat, so the one contract that
must never regress — in CI, where no real server exists — is that it always
issues ``DELETE /session`` from a ``finally``, even when a step half-way
through raises. These tests drive ``run_probe`` against a mocked httpx client
and assert exactly that; they never open a real session.
"""

from __future__ import annotations

import pytest

from backend.app.services.catdv_client import CatdvClient
from tools.probe_catdv_writeback import run_probe


class FakeHttp:
    """Minimal stand-in for ``httpx.AsyncClient`` recording the method+path of
    every call. A ``fail_on`` tuple makes the matching (method, path-substring)
    request raise, to simulate a failure part-way through the probe."""

    def __init__(self, clip: dict, *, fail_on: tuple[str, str] | None = None) -> None:
        self._clip = clip
        self._fail_on = fail_on
        self.calls: list[tuple[str, str]] = []

    def _maybe_fail(self, method: str, url: str) -> None:
        if self._fail_on and self._fail_on[0] == method and self._fail_on[1] in url:
            raise RuntimeError(f"boom on {method} {url}")

    def _ok(self, data):
        class _Resp:
            status_code = 200

            def json(self):
                return {"status": "OK", "data": data}

        return _Resp()

    async def post(self, url, *a, **kw):  # login -> POST /session
        self.calls.append(("POST", url))
        self._maybe_fail("POST", url)
        return self._ok({})

    async def request(self, method, url, *a, **kw):  # GET / PUT /clips/{id}
        self.calls.append((method, url))
        self._maybe_fail(method, url)
        return self._ok(self._clip)

    async def delete(self, url, *a, **kw):  # logout -> DELETE /session
        self.calls.append(("DELETE", url))
        return self._ok({})

    async def aclose(self):
        pass


def _client_with(http: FakeHttp) -> CatdvClient:
    client = CatdvClient(base_url="http://fake", username="u", password="p")
    client._client = http  # type: ignore[assignment]
    return client


def _deleted_session(http: FakeHttp) -> bool:
    return any(m == "DELETE" and url.endswith("/api/9/session") for m, url in http.calls)


async def test_probe_releases_seat_on_success():
    """Happy path: the probe restores the clip AND deletes the session."""
    http = FakeHttp({"ID": 1, "markers": [], "fields": {}})
    result = await run_probe(_client_with(http), clip_id=1)
    assert result.restored is True
    assert _deleted_session(http), "probe must DELETE /session on the happy path"


async def test_probe_releases_seat_when_a_step_raises():
    """The load-bearing contract: a PUT that blows up mid-probe must NOT leak
    the seat — ``DELETE /session`` still runs from the ``finally``."""
    http = FakeHttp({"ID": 1, "markers": [], "fields": {}}, fail_on=("PUT", "/clips/1"))
    with pytest.raises(RuntimeError, match="boom"):
        await run_probe(_client_with(http), clip_id=1)
    assert _deleted_session(http), "probe must DELETE /session even when a step raises"


async def test_probe_never_leaves_session_logged_in():
    """After the probe, the client is logged out (its seat is free for the next
    caller), regardless of outcome."""
    http = FakeHttp({"ID": 1, "markers": [], "fields": {}})
    client = _client_with(http)
    await run_probe(client, clip_id=1)
    assert client.logged_in is False
