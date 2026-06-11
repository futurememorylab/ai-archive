# tests/unit/test_catdv_client_activity.py
"""last_activity is stamped by real API calls but NOT by the health probe,
so idle auto-disconnect can't be starved by the 30s background probe."""

import httpx
import pytest

from backend.app.services.catdv_client import CatdvClient


def _envelope_ok():
    return {"status": "OK", "data": {}}


@pytest.mark.asyncio
async def test_logged_in_property_reflects_login(monkeypatch):
    client = CatdvClient("http://example.invalid", "u", "p")
    async with client:
        assert client.logged_in is False

        async def fake_post(url, **kw):
            return httpx.Response(200, json={"status": "OK"}, request=httpx.Request("POST", url))

        monkeypatch.setattr(client.http, "post", fake_post)
        await client.login()
        assert client.logged_in is True


@pytest.mark.asyncio
async def test_real_call_stamps_activity_but_health_does_not(monkeypatch):
    client = CatdvClient("http://example.invalid", "u", "p")
    async with client:
        client._logged_in = True
        client._last_activity = 0.0

        async def fake_request(method, url, **kw):
            return httpx.Response(200, json=_envelope_ok(), request=httpx.Request(method, url))

        monkeypatch.setattr(client.http, "request", fake_request)

        # health() must not stamp activity
        await client.health()
        assert client.last_activity == 0.0

        # a real call must stamp it
        await client.get_clip(1)
        assert client.last_activity > 0.0
