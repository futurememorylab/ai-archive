"""Shutdown budget: Cloud Run grants 10s after SIGTERM, shared by the
lifespan (CatDV logout) and Litestream's final WAL sync. logout() must
bound its request so a dead tunnel can't starve the sync."""

import httpx

from backend.app.services.catdv_client import CatdvClient


async def test_logout_uses_short_timeout(monkeypatch):
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
    assert captured.get("timeout") == 3.0
