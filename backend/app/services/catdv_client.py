"""CatdvClient — thin async HTTP wrapper over the CatDV Enterprise REST
API. Handles login/relogin, session lifecycle, and the busy-server
(seat-limit) signal. Used by the CatDV archive adapter and the proxy
resolver."""

import asyncio
from pathlib import Path
from typing import Any, Self

import httpx

from backend.app.models.catdv import Envelope


class CatdvAuthError(RuntimeError):
    """Raised when the CatDV server rejects credentials."""


class CatdvError(RuntimeError):
    """Raised for non-AUTH ERROR envelopes."""


class CatdvBusyError(RuntimeError):
    """Raised when the CatDV server is at its concurrent-session limit."""


class CatdvClient:
    """Thin async wrapper around CatDV REST. One client per app process.

    Re-authenticates transparently when the server returns an AUTH envelope.
    """

    def __init__(
        self, base_url: str, username: str, password: str, timeout_secs: float = 60.0
    ) -> None:
        self._base = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._client: httpx.AsyncClient | None = None
        self._login_lock = asyncio.Lock()
        self._timeout = timeout_secs
        self._logged_in = False

    async def __aenter__(self) -> Self:
        self._client = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, *exc_info) -> None:
        if self._client is not None:
            if self._logged_in:
                try:
                    await self.logout()
                except Exception:
                    pass
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
            if env.is_busy:
                raise CatdvBusyError(env.error_message or "CatDV session limit reached")
            if not env.is_ok:
                raise CatdvAuthError(env.error_message or "login rejected")
            self._logged_in = True

    async def logout(self) -> None:
        """Best-effort DELETE /session so we don't orphan a server-side slot."""
        if self._client is None or not self._logged_in:
            return
        try:
            await self.http.delete(f"{self._base}/catdv/api/9/session")
        finally:
            self._logged_in = False

    async def _call_json(self, method: str, path: str, *, json: Any = None) -> Envelope:
        """Issue a JSON request. Re-login once on AUTH; raise on ERROR."""
        url = f"{self._base}{path}"
        resp = await self.http.request(method, url, json=json)
        env = Envelope.model_validate(resp.json())
        if env.requires_reauth:
            await self.login()
            resp = await self.http.request(method, url, json=json)
            env = Envelope.model_validate(resp.json())
        if env.is_busy:
            raise CatdvBusyError(env.error_message or "CatDV session limit reached")
        if not env.is_ok:
            raise CatdvError(env.error_message or "CatDV ERROR")
        return env

    async def list_clips(
        self, catalog_id: int, *, offset: int = 0, limit: int = 100, q: str | None = None
    ) -> dict[str, Any]:
        # CatDV's GET /api/9/clips ignores `query` when `catalogID` is also
        # passed as a URL parameter, so the catalogue filter has to live
        # inside the query expression. Paging uses `skip`/`take` (the
        # documented names); `offset`/`limit` are accepted but corrupt the
        # response's `totalItems` field (it ends up reporting the page size
        # instead of the full result-set size). The query language is
        # parenthesised triples joined with `and`/`or`; see
        # https://docs.squarebox.com/catdv-server/rest-api/REST-API-Reference.html
        clauses = [f"((catalog.ID)eq({catalog_id}))"]
        if q:
            sanitised = q.replace("(", "").replace(")", "")
            clauses.append(f"((clip.name)contains({sanitised}))")
        params: dict[str, str] = {
            "query": "and".join(clauses),
            "skip": str(offset),
            "take": str(limit),
        }
        url = "/catdv/api/9/clips"
        env = await self._call_json_with_params("GET", url, params=params)
        return env.data

    async def _call_json_with_params(
        self, method: str, path: str, *, params: dict[str, str] | None = None
    ) -> Envelope:
        url = f"{self._base}{path}"
        resp = await self.http.request(method, url, params=params)
        env = Envelope.model_validate(resp.json())
        if env.requires_reauth:
            await self.login()
            resp = await self.http.request(method, url, params=params)
            env = Envelope.model_validate(resp.json())
        if env.is_busy:
            raise CatdvBusyError(env.error_message or "CatDV session limit reached")
        if not env.is_ok:
            raise CatdvError(env.error_message or "CatDV ERROR")
        return env

    async def download_proxy(self, clip_id: int, dest: Path, chunk_size: int = 1024 * 1024) -> None:
        """Stream the proxy for a clip to `dest`. Resumes from existing partial file."""
        if not self._logged_in:
            await self.login()
        url = f"{self._base}/catdv/api/9/clips/{clip_id}/media"
        existing_size = dest.stat().st_size if dest.exists() else 0
        headers: dict[str, str] = {}
        if existing_size > 0:
            headers["Range"] = f"bytes={existing_size}-"

        async with self.http.stream("GET", url, headers=headers) as resp:
            if resp.status_code == 401 or _is_auth_envelope(resp):
                await self.login()
                async with self.http.stream("GET", url, headers=headers) as resp2:
                    resp2.raise_for_status()
                    await self._stream_to_file(
                        resp2, dest, append=existing_size > 0, chunk_size=chunk_size
                    )
                    return
            resp.raise_for_status()
            await self._stream_to_file(resp, dest, append=existing_size > 0, chunk_size=chunk_size)

    async def _stream_to_file(
        self, resp: httpx.Response, dest: Path, *, append: bool, chunk_size: int
    ) -> None:
        mode = "ab" if append else "wb"
        dest.parent.mkdir(parents=True, exist_ok=True)
        # File writes hop to a worker thread so the event loop stays
        # responsive while we ingest a multi-hundred-MB proxy stream.
        with open(dest, mode) as f:  # noqa: ASYNC230
            async for chunk in resp.aiter_bytes(chunk_size):
                await asyncio.to_thread(f.write, chunk)

    async def put_clip(self, clip_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        env = await self._call_json("PUT", f"/catdv/api/9/clips/{clip_id}", json=payload)
        return env.data

    async def get_clip(self, clip_id: int) -> dict[str, Any]:
        env = await self._call_json("GET", f"/catdv/api/9/clips/{clip_id}")
        return env.data

    async def health(self) -> dict[str, Any]:
        """Cheap reachability probe. Returns the envelope `data` payload
        (which may be {}) on OK; raises CatdvError/CatdvAuthError otherwise.

        The endpoint `GET /catdv/api/info` is documented as anonymous and
        cheap; some installs require auth, so we re-login on AUTH via the
        shared `_call_json` helper.
        """
        env = await self._call_json("GET", "/catdv/api/info")
        return env.data or {}

    async def list_fields(self) -> list[dict[str, Any]]:
        env = await self._call_json("GET", "/catdv/api/9/fields")
        data = env.data
        if isinstance(data, dict):
            items = data.get("fields") or []
        elif isinstance(data, list):
            items = data
        else:
            items = []
        return list(items)


def _is_auth_envelope(resp: "httpx.Response") -> bool:
    """Detect CatDV's 'HTTP 200 + AUTH envelope' anti-pattern on the media endpoint.

    The proxy stream endpoint serves video bytes (Content-Type: video/quicktime).
    When the session is missing, the server returns an HTTP 200 JSON envelope
    instead — we must catch that before writing JSON bytes into a .mov file.
    """
    ct = resp.headers.get("content-type", "")
    return "json" in ct.lower()
