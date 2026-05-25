import logging

import pytest

from backend.app.services.catdv_client import CatdvBusyError, CatdvClient
from tests.fakes.fake_catdv import running_fake_catdv


@pytest.mark.asyncio
async def test_exit_calls_logout_when_logged_in():
    with running_fake_catdv() as (base_url, fake):
        client = CatdvClient(base_url=base_url, username="klientAI", password="secret")
        async with client:
            await client.login()
        assert fake.logout_count == 1


@pytest.mark.asyncio
async def test_exit_skips_logout_when_never_logged_in():
    with running_fake_catdv() as (base_url, fake):
        client = CatdvClient(base_url=base_url, username="klientAI", password="secret")
        async with client:
            pass
        assert fake.logout_count == 0


@pytest.mark.asyncio
async def test_busy_envelope_raises_catdv_busy_error():
    class BusyResponse:
        def json(self):
            return {
                "status": "BUSY",
                "errorMessage": "Web Client session limit reached (Maximum:2).",
                "data": None,
            }

    with running_fake_catdv() as (base_url, fake):
        client = CatdvClient(base_url=base_url, username="klientAI", password="secret")
        async with client:

            async def fake_post(*args, **kwargs):
                return BusyResponse()

            client._client.post = fake_post  # type: ignore[assignment]
            with pytest.raises(CatdvBusyError):
                await client.login()
        assert fake.logout_count == 0
        assert client._logged_in is False


@pytest.mark.asyncio
async def test_logout_logs_warning_on_delete_failure(caplog):
    with running_fake_catdv() as (base_url, _fake):
        client = CatdvClient(base_url=base_url, username="klientAI", password="secret")
        async with client:
            await client.login()

            async def boom(*args, **kwargs):
                raise RuntimeError("network down")

            client._client.delete = boom  # type: ignore[assignment]
            with caplog.at_level(logging.WARNING):
                await client.logout()
        assert client._logged_in is False
        assert any(
            "seat" in r.message.lower() or "logout" in r.message.lower() for r in caplog.records
        )
