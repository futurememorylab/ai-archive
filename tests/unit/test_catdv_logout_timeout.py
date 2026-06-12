"""Shutdown budget: Cloud Run grants 10s after SIGTERM, shared by the
lifespan (CatDV logout) and Litestream's final WAL sync. logout() must
bound its request (and keep it tight) so a dead tunnel can't starve the
sync. See ADR 0077."""

import httpx

from backend.app.services import catdv_client
from backend.app.services.catdv_client import CatdvClient


def test_logout_timeout_is_two_seconds():
    # Tight budget: onetun kill (2s) + this logout (2s) leaves ~6s of the
    # 10s SIGTERM grace for Litestream's final sync.
    assert catdv_client.LOGOUT_TIMEOUT_S == 2.0


async def test_logout_applies_the_short_timeout(monkeypatch):
    client = CatdvClient("http://example.invalid", "u", "p")
    captured: dict = {}

    async with client:
        client._logged_in = True

        async def fake_delete(url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return httpx.Response(200, request=httpx.Request("DELETE", url))

        monkeypatch.setattr(client.http, "delete", fake_delete)
        await client.logout()

    assert captured["url"].endswith("/catdv/api/9/session")
    assert captured.get("timeout") == catdv_client.LOGOUT_TIMEOUT_S
