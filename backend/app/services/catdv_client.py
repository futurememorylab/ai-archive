import asyncio
from typing import Any, Self

import httpx

from backend.app.models.catdv import Envelope


class CatdvAuthError(RuntimeError):
    """Raised when the CatDV server rejects credentials."""


class CatdvError(RuntimeError):
    """Raised for non-AUTH ERROR envelopes."""


class CatdvClient:
    """Thin async wrapper around CatDV REST. One client per app process.

    Re-authenticates transparently when the server returns an AUTH envelope.
    """

    def __init__(self, base_url: str, username: str, password: str,
                 timeout_secs: float = 60.0) -> None:
        self._base = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._client: httpx.AsyncClient | None = None
        self._login_lock = asyncio.Lock()
        self._timeout = timeout_secs

    async def __aenter__(self) -> Self:
        self._client = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, *exc_info) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def http(self) -> httpx.AsyncClient:
        assert self._client is not None, "CatdvClient must be used as async context manager"
        return self._client

    async def login(self) -> None:
        async with self._login_lock:
            resp = await self.http.post(
                f"{self._base}/catdv/api/9/session",
                json={"username": self._username, "password": self._password},
            )
            env = Envelope.model_validate(resp.json())
            if not env.is_ok:
                raise CatdvAuthError(env.error_message or "login rejected")

    async def _call_json(self, method: str, path: str, *, json: Any = None) -> Envelope:
        """Issue a JSON request. Re-login once on AUTH; raise on ERROR."""
        url = f"{self._base}{path}"
        resp = await self.http.request(method, url, json=json)
        env = Envelope.model_validate(resp.json())
        if env.requires_reauth:
            await self.login()
            resp = await self.http.request(method, url, json=json)
            env = Envelope.model_validate(resp.json())
        if not env.is_ok:
            raise CatdvError(env.error_message or "CatDV ERROR")
        return env

    async def list_clips(self, catalog_id: int, *, offset: int = 0,
                          limit: int = 100, q: str | None = None) -> dict[str, Any]:
        params: dict[str, str] = {"offset": str(offset), "limit": str(limit)}
        if q:
            params["q"] = q
        url = f"/catdv/api/9/catalogs/{catalog_id}/clips"
        env = await self._call_json_with_params("GET", url, params=params)
        return env.data

    async def _call_json_with_params(self, method: str, path: str, *,
                                      params: dict[str, str] | None = None) -> Envelope:
        url = f"{self._base}{path}"
        resp = await self.http.request(method, url, params=params)
        env = Envelope.model_validate(resp.json())
        if env.requires_reauth:
            await self.login()
            resp = await self.http.request(method, url, params=params)
            env = Envelope.model_validate(resp.json())
        if not env.is_ok:
            raise CatdvError(env.error_message or "CatDV ERROR")
        return env

    async def get_clip(self, clip_id: int) -> dict[str, Any]:
        env = await self._call_json("GET", f"/catdv/api/9/clips/{clip_id}")
        return env.data
