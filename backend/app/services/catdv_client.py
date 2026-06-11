"""CatdvClient — thin async HTTP wrapper over the CatDV Enterprise REST
API. Handles login/relogin, session lifecycle, and the busy-server
(seat-limit) signal. Used by the CatDV archive adapter and the proxy
resolver."""

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Any, Self

import httpx

from backend.app.models.catdv import Envelope

_DEFAULT_CHUNK = 1 << 16

_QUERY_ALLOWLIST = re.compile(r"[^\w\s\-.]", re.UNICODE)


def _sanitise_query(q: str) -> str:
    """Strip any character not in the conservative allowlist
    (alphanumeric, whitespace, hyphen, underscore, dot).

    The CatDV REST query language is parenthesised triples joined with
    `and`/`or`. The undocumented escape rules make per-character escaping
    unreliable, so we instead remove anything that could let user input
    escape its embedding in `(clip.name)contains(<here>)`.
    """
    return _QUERY_ALLOWLIST.sub("", q)


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
        self._last_activity: float = 0.0

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

    @property
    def logged_in(self) -> bool:
        return self._logged_in

    @property
    def last_activity(self) -> float:
        """Monotonic timestamp of the last operator-driven API call (0.0 if
        none yet). The health probe deliberately does not update this."""
        return self._last_activity

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
            self._last_activity = time.monotonic()

    async def logout(self) -> None:
        """Best-effort DELETE /session so we don't orphan a server-side slot.

        Logs a WARNING (rather than failing silently) if the call errors, so
        a possibly-leaked license seat is at least diagnosable in the journal.
        """
        if self._client is None or not self._logged_in:
            return
        try:
            # Bounded: on shutdown this shares Cloud Run's 10s SIGTERM
            # grace with Litestream's final WAL sync; a dead tunnel must
            # not starve it. 3s matches uvicorn --timeout-graceful-shutdown.
            await self.http.delete(f"{self._base}/catdv/api/9/session", timeout=3.0)
        except Exception:
            logging.getLogger(__name__).warning(
                "CatDV logout (DELETE /session) failed; the license seat may "
                "remain held until the server times it out",
                exc_info=True,
            )
        finally:
            self._logged_in = False

    async def _call_json(
        self, method: str, path: str, *, json: Any = None, reauth: bool = True,
        track_activity: bool = True,
    ) -> Envelope:
        """Issue a JSON request. Re-login once on AUTH (unless reauth=False); raise on ERROR."""
        url = f"{self._base}{path}"
        resp = await self.http.request(method, url, json=json)
        env = Envelope.model_validate(resp.json())
        if env.requires_reauth:
            if not reauth:
                raise CatdvAuthError(env.error_message or "not authenticated")
            await self.login()
            resp = await self.http.request(method, url, json=json)
            env = Envelope.model_validate(resp.json())
        if env.is_busy:
            raise CatdvBusyError(env.error_message or "CatDV session limit reached")
        if not env.is_ok:
            raise CatdvError(env.error_message or "CatDV ERROR")
        if track_activity:
            self._last_activity = time.monotonic()
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
            sanitised = _sanitise_query(q)
            clauses.append(f"((clip.name)contains({sanitised}))")
        params: dict[str, str] = {
            "query": "and".join(clauses),
            "skip": str(offset),
            "take": str(limit),
            # The bulk endpoint omits user-defined fields and markers by
            # default; request them so the clips list can show year/decade
            # and the marker count without a per-clip round-trip.
            "include": "clip.fields,markers",
        }
        url = "/catdv/api/9/clips"
        env = await self._call_json_with_params("GET", url, params=params)
        return env.data

    async def _call_json_with_params(
        self, method: str, path: str, *, params: dict[str, str] | None = None,
        track_activity: bool = True,
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
        if track_activity:
            self._last_activity = time.monotonic()
        return env

    async def download_proxy(self, clip_id: int, dest: Path, chunk_size: int = 1024 * 1024) -> None:
        """Stream the proxy for a clip to `dest`. Resumes from existing partial file."""
        if not self._logged_in:
            await self.login()
        url = f"{self._base}/catdv/api/9/clips/{clip_id}/media"
        existing_size = dest.stat().st_size if dest.exists() else 0  # sync-io-ok: pre-existing, tracked for the tier-4 async-io pass
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

    async def download_original(
        self, media_id: int, dest: Path, chunk_size: int = 1024 * 1024
    ) -> None:
        """Stream a clip's ORIGINAL source file (not the proxy) to `dest`.

        Used for stills, which have no generated proxy. Hits
        `GET /api/9/media/{media_id}?type=orig` (the `media_id` comes from
        the clip's `provider_data["media"]["ID"]`). `type` defaults to
        `proxy` server-side, which 404s for stills — `orig` is required.
        Same HTTP-200-AUTH-envelope guard as `download_proxy`.
        """
        if not self._logged_in:
            await self.login()
        url = f"{self._base}/catdv/api/9/media/{media_id}"
        params = {"type": "orig"}
        async with self.http.stream("GET", url, params=params) as resp:
            if resp.status_code == 401 or _is_auth_envelope(resp):
                await self.login()
                async with self.http.stream("GET", url, params=params) as resp2:
                    resp2.raise_for_status()
                    await self._stream_to_file(resp2, dest, append=False, chunk_size=chunk_size)
                    return
            resp.raise_for_status()
            await self._stream_to_file(resp, dest, append=False, chunk_size=chunk_size)

    async def download_thumbnail(
        self, thumb_id: int, dest: Path, *, width: int | None = None, fmt: str = "jpg"
    ) -> None:
        """Stream a thumbnail/poster image to `dest`.

        Hits the singular image renderer `GET /api/9/thumbnail/{id}` (the
        plural `/thumbnails/{id}` is the JSON metadata endpoint — do not use
        it). When the session is missing CatDV answers HTTP 200 with a JSON
        AUTH envelope instead of image bytes; `_is_auth_envelope` catches that
        via the content-type so we re-login rather than writing JSON into a
        .jpg.
        """
        if not self._logged_in:
            await self.login()
        url = f"{self._base}/catdv/api/9/thumbnail/{thumb_id}"
        params: dict[str, str] = {"fmt": fmt}
        if width:
            params["width"] = str(width)

        async with self.http.stream("GET", url, params=params) as resp:
            if resp.status_code == 401 or _is_auth_envelope(resp):
                await self.login()
                async with self.http.stream("GET", url, params=params) as resp2:
                    resp2.raise_for_status()
                    await self._stream_to_file(resp2, dest, append=False, chunk_size=_DEFAULT_CHUNK)
                    return
            resp.raise_for_status()
            await self._stream_to_file(resp, dest, append=False, chunk_size=_DEFAULT_CHUNK)

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
        (which may be {}) on OK; raises CatdvAuthError without re-login
        on missing session; raises CatdvError/CatdvBusyError otherwise.

        A re-login here would itself take the seat the probe is looking
        for. The connection monitor treats any raise as 'offline', so
        propagating CatdvAuthError is the right behaviour — Reconnect
        button triggers a login when the user is ready to spend a seat.
        """
        env = await self._call_json("GET", "/catdv/api/info", reauth=False, track_activity=False)
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
