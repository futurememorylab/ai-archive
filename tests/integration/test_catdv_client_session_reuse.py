"""A replaced Cloud Run instance often can't log out (its tunnel is being
taken over by the new instance), leaking a CatDV seat until the server's idle
timeout. On 2026-10-15 three such leaks filled both seats. The client now
remembers its JSESSIONID and a new process adopts it instead of spending a
new seat."""

import pytest

from backend.app.services.catdv_client import CatdvClient
from tests.fakes.fake_catdv import running_fake_catdv


class _MemoryStore:
    def __init__(self, sid: str | None = None):
        self.sid = sid

    async def get(self) -> str | None:
        return self.sid

    async def set(self, sid: str) -> None:
        self.sid = sid

    async def clear(self) -> None:
        self.sid = None


def _client(base_url, store):
    return CatdvClient(
        base_url=base_url, username="klientAI", password="secret", session_store=store
    )


@pytest.mark.asyncio
async def test_login_stores_session_and_next_process_adopts_it():
    store = _MemoryStore()
    with running_fake_catdv() as (base_url, fake):
        first = _client(base_url, store)
        await first.__aenter__()
        await first.login()
        assert store.sid == "fake-session"
        # The old instance dies without logging out (tunnel gone).
        await first.http.aclose()

        second = _client(base_url, store)
        async with second:
            await second.login()
            assert fake.login_call_count == 1, "must reuse the seat, not take a new one"
            assert second.logged_in
            assert await second.list_fields() is not None


@pytest.mark.asyncio
async def test_stale_stored_session_falls_back_to_fresh_login():
    store = _MemoryStore("expired-session")
    with running_fake_catdv() as (base_url, fake):
        async with _client(base_url, store) as client:
            await client.login()
            assert fake.login_call_count == 1
            assert store.sid == "fake-session"


@pytest.mark.asyncio
async def test_clean_logout_forgets_the_session():
    store = _MemoryStore()
    with running_fake_catdv() as (base_url, _fake):
        async with _client(base_url, store) as client:
            await client.login()
        assert store.sid is None
