"""Boot the real app in-process for Playwright.

Playwright needs a real socket, and the UI needs a numeric-keyed archive that
only injection (not env) can supply. So: run uvicorn.Server on a daemon thread,
then install_live_ctx with our fakes and seed the DB on the server's own event
loop (so the aiosqlite connection is used from the loop that owns it).
"""

from __future__ import annotations

import asyncio
import importlib
import os
import shutil
import threading
import time
from pathlib import Path

import uvicorn

from tests.walkthrough import seed
from tests.walkthrough.fakes import (
    CATALOG_ID,
    FakeArchive,
    LocalFileResolver,
    StubThumbnailService,
    build_clips,
)

_OFFLINE_ENV = {
    "APP_ENV": "dev",
    "CATDV_OFFLINE": "true",
    "CATDV_BASE_URL": "http://localhost:0",
    "CATDV_USERNAME": "",
    "CATDV_PASSWORD": "",
    "CATDV_CATALOG_ID": CATALOG_ID,
    "GCP_PROJECT_ID": "test-project",
    "GCS_BUCKET_NAME": "test-bucket",
    "INSTANCE_ID": "test-instance",
    "PROXY_SOURCE": "rest",
}


class WalkthroughApp:
    def __init__(self, data_dir: Path, port: int = 8766) -> None:
        self.data_dir = Path(data_dir)
        self.port = port
        self.base_url = f"http://127.0.0.1:{port}"
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._app = None
        self._thumb: Path | None = None

    def start(self) -> None:
        for k, v in _OFFLINE_ENV.items():
            os.environ.setdefault(k, v)
        # Force-empty the external credentials (override, not setdefault): if a
        # developer has real CATDV creds exported, init_external would be True
        # and GeminiService/GcsService would be constructed (google.auth.default()).
        # Empty creds keep the boot unconditionally offline. The CatDV seat is
        # already safe via CATDV_OFFLINE=true, but this makes "fully offline" hold
        # regardless of the ambient environment.
        os.environ["CATDV_USERNAME"] = ""
        os.environ["CATDV_PASSWORD"] = ""
        os.environ["DATA_DIR"] = str(self.data_dir)
        os.environ["BIND_PORT"] = str(self.port)

        from backend.app import main as main_mod

        importlib.reload(main_mod)
        self._app = main_mod.app

        video = seed.make_proxy_video(self.data_dir / "proxy_101.mp4")
        self._thumb = seed.make_thumbnail(self.data_dir / "thumb.jpg", video)
        clips = build_clips(video)

        # Seed a standalone template DB on its own connection, then connect a
        # COPY of it for the run: seeding and the live run use separate database
        # files. The app opens DATA_DIR/app.db (context.py), so the copy lands
        # there. Done before boot — the app's lifespan opens the already-seeded
        # copy; its idempotent migrations + stale-session cleanup leave the seed
        # intact (run_startup_cleanup only prunes live_sessions). The catalog is
        # seeded into clip_list_cache so the annotation-status filters resolve.
        seed_template = self.data_dir / "seed_template.db"
        run_db = self.data_dir / "app.db"
        asyncio.run(seed.build_seed_db(seed_template, clips=clips, catalog_id=CATALOG_ID))
        shutil.copyfile(seed_template, run_db)

        config = uvicorn.Config(
            self._app, host="127.0.0.1", port=self.port, log_level="warning"
        )
        self._server = uvicorn.Server(config)

        def _run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            loop.run_until_complete(self._server.serve())

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        self._wait_until_started()

        # The DB was seeded into app.db before boot (above); the app's own
        # connection is the single live connection for this run.
        core = self._app.state.core_ctx

        from tests._helpers.live_ctx import install_live_ctx

        resolver = LocalFileResolver(video)
        live = install_live_ctx(
            self._app,
            archive=FakeArchive(clips),
            proxy_resolver=resolver,
            thumbnail_service=StubThumbnailService(self._thumb),
        )
        # build_context only wires media_cache_backend when init_external is
        # True (real proxy_resolver present); offline boot leaves it None, so
        # /api/media/{id} would 503 (media_cache.py:80). Build the same
        # local-proxy backend over our injected resolver here, mirroring
        # context.py's `if arch.proxy_resolver is not None` wiring. ai_store /
        # gcs are the install_live_ctx MagicMock defaults; LocalProxyBackend
        # tries local first and our resolver always hits, so they're unused.
        from backend.app.services.media_cache import build_media_cache_backend

        live.media_cache_backend = build_media_cache_backend(
            media_cache="local",
            resolver=resolver,
            ai_store=live.ai_store,
            gcs=live._gcs_service,
            proxy_cache_repo=core.proxy_cache_repo,
            db_provider=lambda: core.db,
        )

    def _wait_until_started(self, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._server is not None and self._server.started:
                if getattr(self._app.state, "core_ctx", None) is not None:
                    return
            time.sleep(0.05)
        raise RuntimeError("walkthrough app failed to start within timeout")

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=10)
