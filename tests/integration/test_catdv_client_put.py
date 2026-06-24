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
async def test_put_clip_declares_utf8_charset():
    """Root-cause guard: every JSON write must carry an explicit UTF-8 charset.
    A bare `application/json` body is decoded by CatDV's servlet as ISO-8859-1,
    which is what used to turn our UTF-8 into compounding mojibake on every
    write. The header fixes it at the source."""
    captured: dict = {}
    client = CatdvClient(base_url="http://fake", username="u", password="p")

    class FakeResp:
        status_code = 200

        def json(self):
            return {"status": "OK", "data": {"ID": 1}}

    class FakeClient:
        async def request(self, *a, **kw):
            captured.update(kw)
            return FakeResp()

        async def aclose(self):
            pass

    client._client = FakeClient()
    await client.put_clip(1, {"markers": [{"name": "Záběr řeky"}]})
    assert captured["headers"] == {"Content-Type": "application/json; charset=utf-8"}


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


def test_utf8_json_body_read_as_latin1_is_the_mojibake_source():
    """Pins the root cause the charset header fixes: a real UTF-8 JSON request
    body, decoded as latin-1 (the servlet default when no charset is declared),
    IS the mojibake we used to store — while decoded as utf-8 it is clean. This
    is why declaring charset=utf-8 prevents the damage at the source."""
    import json

    import httpx

    payload = {"category": "Interiér", "name": "Záběr řeky"}
    body = httpx.Request("PUT", "http://x", json=payload).content  # what we send

    misread = json.loads(body.decode("latin-1"))  # CatDV decoding without charset
    assert misread["category"] == "InteriÃ©r"  # the corruption we used to store

    correct = json.loads(body.decode("utf-8"))  # CatDV decoding WITH charset=utf-8
    assert correct == payload  # clean — exactly what we sent
