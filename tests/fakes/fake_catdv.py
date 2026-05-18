import contextlib
import socket
import threading
import time
from typing import Iterator

import uvicorn
from fastapi import FastAPI, Request, Response


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeCatdv:
    """In-process fake CatDV server controllable from tests."""

    def __init__(self) -> None:
        self.app = FastAPI()
        self.session_cookie = "JSESSIONID=fake-session"
        self.valid_creds = {"klientAI": "secret"}
        self.clips: dict[int, dict] = {}
        self.proxies: dict[int, bytes] = {}
        self.force_auth_until: float = 0.0
        self.put_log: list[tuple[int, dict]] = []
        self._register_routes()

    def _envelope(self, status: str, data=None, msg: str | None = None) -> dict:
        return {"status": status, "errorMessage": msg, "data": data}

    def _register_routes(self) -> None:
        @self.app.post("/catdv/api/9/session")
        async def login(req: Request):
            body = await req.json()
            if self.valid_creds.get(body.get("username")) == body.get("password"):
                response = Response(content='{"status":"OK","errorMessage":null,"data":null}',
                                    media_type="application/json")
                response.set_cookie("JSESSIONID", "fake-session")
                return response
            return self._envelope("ERROR", msg="Invalid user name or password")

        @self.app.get("/catdv/api/9/clips/{clip_id}")
        async def get_clip(clip_id: int, request: Request):
            if time.time() < self.force_auth_until or request.cookies.get("JSESSIONID") != "fake-session":
                return self._envelope("AUTH")
            clip = self.clips.get(clip_id)
            if not clip:
                return self._envelope("ERROR", msg="Not found")
            return self._envelope("OK", data=clip)

        @self.app.put("/catdv/api/9/clips/{clip_id}")
        async def put_clip(clip_id: int, request: Request):
            if request.cookies.get("JSESSIONID") != "fake-session":
                return self._envelope("AUTH")
            body = await request.json()
            self.put_log.append((clip_id, body))
            existing = self.clips.get(clip_id, {})
            existing.update(body)
            self.clips[clip_id] = existing
            return self._envelope("OK", data={"ID": clip_id, "modifyDate": "2026-05-18"})

        @self.app.get("/catdv/api/9/clips/{clip_id}/media")
        async def get_media(clip_id: int, request: Request):
            if request.cookies.get("JSESSIONID") != "fake-session":
                return Response(status_code=401)
            blob = self.proxies.get(clip_id)
            if blob is None:
                return Response(status_code=404)
            range_header = request.headers.get("range")
            if range_header and range_header.startswith("bytes="):
                start_s, _, end_s = range_header[6:].partition("-")
                start = int(start_s)
                end = int(end_s) if end_s else len(blob) - 1
                chunk = blob[start:end + 1]
                return Response(
                    content=chunk,
                    status_code=206,
                    media_type="video/quicktime",
                    headers={
                        "Content-Range": f"bytes {start}-{end}/{len(blob)}",
                        "Content-Length": str(len(chunk)),
                        "Accept-Ranges": "bytes",
                    },
                )
            return Response(
                content=blob,
                media_type="video/quicktime",
                headers={"Accept-Ranges": "bytes", "Content-Length": str(len(blob))},
            )


@contextlib.contextmanager
def running_fake_catdv() -> Iterator[tuple[str, FakeCatdv]]:
    """Boot a fake CatDV on a free port. Yields (base_url, fake)."""
    fake = FakeCatdv()
    port = _free_port()
    config = uvicorn.Config(fake.app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 5
    while time.time() < deadline and not server.started:
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}", fake
    finally:
        server.should_exit = True
        thread.join(timeout=5)
