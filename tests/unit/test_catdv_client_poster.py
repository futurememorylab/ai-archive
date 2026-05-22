import httpx
import pytest

from backend.app.services.catdv_client import CatdvClient


@pytest.mark.asyncio
async def test_download_poster_returns_bytes_on_success():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path.endswith("/session"):
            return httpx.Response(200, json={"status": "OK", "data": {}})
        if request.url.path.endswith("/clips/42/poster"):
            return httpx.Response(200, content=b"\xff\xd8\xff\xe0JPEGBYTES")
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with CatdvClient(
        base_url="http://catdv.test",
        username="u",
        password="p",
        transport=transport,
    ) as client:
        data = await client.download_poster(42)

    assert data.startswith(b"\xff\xd8")
    assert "/clips/42/poster" in " ".join(calls)


@pytest.mark.asyncio
async def test_download_poster_reauthenticates_on_401():
    state = {"logged_in_count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/session") and request.method == "POST":
            state["logged_in_count"] += 1
            return httpx.Response(200, json={"status": "OK", "data": {}})
        if request.url.path.endswith("/clips/42/poster"):
            # First GET (after initial login) → 401. Re-login + retry → 200.
            if state["logged_in_count"] < 2:
                return httpx.Response(401)
            return httpx.Response(200, content=b"\xff\xd8AFTERRELOGIN")
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with CatdvClient(
        base_url="http://catdv.test",
        username="u",
        password="p",
        transport=transport,
    ) as client:
        data = await client.download_poster(42)

    assert data == b"\xff\xd8AFTERRELOGIN"
    assert state["logged_in_count"] == 2
