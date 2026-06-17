import pytest

from backend.app.services.catdv_client import CatdvClient, CatdvError
from tests.fakes.fake_catdv import running_fake_catdv


@pytest.mark.asyncio
async def test_put_clip_writes_payload():
    with running_fake_catdv() as (base_url, fake):
        fake.clips[10] = {"ID": 10, "name": "before", "markers": []}
        client = CatdvClient(base_url=base_url, username="klientAI", password="secret")
        async with client:
            await client.login()
            payload = {"markers": [{"name": "scene-1"}]}
            result = await client.put_clip(10, payload)
            assert result["ID"] == 10
        assert fake.put_log == [(10, payload)]
        assert fake.clips[10]["markers"] == [{"name": "scene-1"}]


@pytest.mark.asyncio
async def test_put_clip_raises_catdv_error_on_error_envelope(monkeypatch):
    client = CatdvClient(base_url="http://fake", username="u", password="p")

    class FakeResp:
        status_code = 200

        def json(self):
            return {"status": "ERROR", "errorMessage": "boom", "data": None}

    class FakeClient:
        async def request(self, *a, **kw):
            return FakeResp()

        async def aclose(self):
            pass

    client._client = FakeClient()
    with pytest.raises(CatdvError, match="boom"):
        await client.put_clip(1, {"markers": []})


@pytest.mark.asyncio
async def test_put_clip_raises_catdv_error_on_numeric_status_body():
    # CatDV's server-error body carries a numeric `status` (e.g. 500) our
    # Envelope model can't parse. It must surface as a classified CatdvError,
    # NOT a raw pydantic ValidationError that escapes apply_changes and makes
    # the SyncEngine retry forever with an unreadable message. Publishing
    # audit, anomaly A2.
    client = CatdvClient(base_url="http://fake", username="u", password="p")

    class FakeResp:
        status_code = 200
        text = '{"status": 500, "message": "Internal Server Error"}'

        def json(self):
            return {"status": 500, "message": "Internal Server Error"}

    class FakeClient:
        async def request(self, *a, **kw):
            return FakeResp()

        async def aclose(self):
            pass

    client._client = FakeClient()
    with pytest.raises(CatdvError):
        await client.put_clip(1, {"markers": []})


@pytest.mark.asyncio
async def test_put_clip_raises_catdv_error_on_http_5xx():
    client = CatdvClient(base_url="http://fake", username="u", password="p")

    class FakeResp:
        status_code = 500
        text = "Internal Server Error"

        def json(self):
            raise ValueError("not json")

    class FakeClient:
        async def request(self, *a, **kw):
            return FakeResp()

        async def aclose(self):
            pass

    client._client = FakeClient()
    with pytest.raises(CatdvError, match="HTTP 500"):
        await client.put_clip(1, {"markers": []})
