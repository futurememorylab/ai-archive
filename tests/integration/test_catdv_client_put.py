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
