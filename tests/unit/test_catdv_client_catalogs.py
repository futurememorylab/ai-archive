"""CatDV catalog listing (#124): client GET /catalogs + adapter → CatalogInfo."""

import httpx
import pytest

from backend.app.archive.errors import AuthError, RetryableError
from backend.app.archive.model import CatalogInfo
from backend.app.archive.providers.catdv.adapter import CatdvArchiveAdapter
from backend.app.services.catdv_client import CatdvAuthError, CatdvBusyError, CatdvClient

_CATALOGS = [
    {"ID": 881507, "name": "AI katalog", "readOnly": False},
    {"ID": 913722, "name": "vyber pro AI", "readOnly": True},
]


@pytest.mark.asyncio
async def test_list_catalogs_returns_data_items(monkeypatch):
    client = CatdvClient("http://example.invalid", "u", "p")
    async with client:
        client._logged_in = True
        seen = []

        async def fake_request(method, url, **kw):
            seen.append((method, url))
            return httpx.Response(
                200, json={"status": "OK", "data": _CATALOGS}, request=httpx.Request(method, url)
            )

        monkeypatch.setattr(client.http, "request", fake_request)
        rows = await client.list_catalogs()
    assert seen[0] == ("GET", "http://example.invalid/catdv/api/9/catalogs")
    assert [r["ID"] for r in rows] == [881507, 913722]


class _Client:
    def __init__(self, result=None, exc=None):
        self._result, self._exc = result, exc

    async def list_catalogs(self):
        if self._exc is not None:
            raise self._exc
        return self._result


def _adapter(client):
    return CatdvArchiveAdapter(client=client)


@pytest.mark.asyncio
async def test_catdv_adapter_list_catalogs_maps_to_catalog_info():
    got = await _adapter(_Client(result=_CATALOGS)).list_catalogs()
    assert got == [
        CatalogInfo("881507", "AI katalog", False),
        CatalogInfo("913722", "vyber pro AI", True),
    ]


@pytest.mark.asyncio
async def test_catdv_adapter_list_catalogs_translates_errors():
    with pytest.raises(AuthError):
        await _adapter(_Client(exc=CatdvAuthError("no session"))).list_catalogs()
    with pytest.raises(RetryableError):
        await _adapter(_Client(exc=CatdvBusyError("max"))).list_catalogs()
