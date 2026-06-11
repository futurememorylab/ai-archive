"""FastAPI app factory and lifespan. Wires routers, mounts static assets,
and owns the context lifecycle (build CoreCtx + LiveCtx at startup, aclose
at shutdown to release the CatDV session seat)."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from backend.app.context import build_context
from backend.app.logging_setup import configure_logging
from backend.app.routes.batches import router as batches_router
from backend.app.routes.cache import api_router as cache_api_router
from backend.app.routes.cache import page_router as cache_page_router
from backend.app.routes.cache import ui_router as cache_ui_router
from backend.app.routes.catdv import router as catdv_router
from backend.app.routes.connection import router as connection_router
from backend.app.routes.events import router as events_router
from backend.app.routes.jobs import router as jobs_router
from backend.app.routes.live import router as live_router
from backend.app.routes.media import router as media_router
from backend.app.routes.pages import page_routers
from backend.app.routes.prompts import router as prompts_router
from backend.app.routes.review import router as review_router
from backend.app.routes.studio import router as studio_router
from backend.app.routes.sync import router as sync_router
from backend.app.routes.ui import router as ui_router
from backend.app.routes.workspaces import router as workspaces_router
from backend.app.seed import seed_default_prompt, seed_live_system_instruction
from backend.app.services.connection_monitor import ConnectionState
from backend.app.settings import Settings
from backend.app.startup import run_startup_cleanup, warn_browser_secret_exposure

SEEDS = Path(__file__).resolve().parents[1] / "seeds"
_STATIC_DIR = Path(__file__).resolve().parent / "static"


def _real_external_enabled(s: Settings) -> bool:
    """In dev tests we bypass external services. In real dev usage, set
    APP_ENV=dev and ensure CATDV_* / GCP_* env vars point at real systems."""
    return all(
        [
            s.catdv_base_url,
            s.catdv_username,
            s.catdv_password,
            s.gcp_project_id,
            s.gcs_bucket_name,
        ]
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = Settings()
    warn_browser_secret_exposure(settings)
    init_external = settings.app_env == "prod" or _real_external_enabled(settings)
    core, live = await build_context(settings, init_external=init_external)
    app.state.core_ctx = core
    app.state.live_ctx = live
    # Expose the media-cache backend to every template render (full pages and
    # HTMX fragments) so the cache badge / controls can hide the unused
    # local-media layer when running cloud-backed (media_cache="ai_store").
    from backend.app.routes.pages.templates import templates as _templates

    _templates.env.globals["media_cache"] = settings.media_cache
    seed_path = SEEDS / "default_template.json"
    if seed_path.exists():
        await seed_default_prompt(core.db, seed_path=seed_path)
    image_seed = SEEDS / "image_template.json"
    if image_seed.exists():
        await seed_default_prompt(core.db, seed_path=image_seed)
    live_seed = SEEDS / "live_system_instruction_cs.json"
    if live_seed.exists():
        await seed_live_system_instruction(core.db, seed_path=live_seed)
    await run_startup_cleanup(core.db)
    if live is not None:
        if live.vpn_supervisor is not None:
            await live.vpn_supervisor.start()
        await live.connection_monitor.start()
        if live.idle_disconnector is not None:
            await live.idle_disconnector.start()
        await live.sync_engine.start()
        await live.lru_eviction.start()
        if live.media_prefetcher is not None:
            await live.media_prefetcher.start()
    try:
        yield
    finally:
        await (live or core).aclose()


def register_routers(app: FastAPI) -> None:
    app.include_router(prompts_router)
    app.include_router(catdv_router)
    app.include_router(jobs_router)
    app.include_router(batches_router)
    app.include_router(review_router)
    app.include_router(media_router)
    app.include_router(events_router)
    app.include_router(connection_router)
    app.include_router(workspaces_router)
    app.include_router(studio_router)
    app.include_router(sync_router)
    app.include_router(ui_router)
    app.include_router(cache_api_router)
    app.include_router(cache_page_router)
    app.include_router(cache_ui_router)
    for r in page_routers:
        app.include_router(r)
    app.include_router(live_router)


app = FastAPI(title="CatDV Annotator", lifespan=lifespan)
_STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


_timing_log = logging.getLogger("backend.app.timing")


@app.middleware("http")
async def _request_timing(request: Request, call_next):
    """Measure wall-clock time per request and expose it.

    - Sets ``X-Process-Time`` header (seconds, 3dp) so DevTools shows it.
    - Logs anything slower than 250 ms at WARNING with method+path+status.
    - Skips ``/static/*`` to keep the log readable.
    """
    is_static = request.url.path.startswith("/static/")
    t0 = perf_counter()
    response = await call_next(request)
    elapsed = perf_counter() - t0
    if not is_static:
        response.headers["X-Process-Time"] = f"{elapsed:.3f}"
        if elapsed >= 0.25:
            _timing_log.warning(
                "slow %s %s -> %d in %.3fs",
                request.method,
                request.url.path,
                response.status_code,
                elapsed,
            )
    return response


@app.middleware("http")
async def _revalidate_static(request: Request, call_next):
    """Force browsers to revalidate static assets (JS/CSS) on every load.

    Without this, browsers serve `/static/*` from memory cache without
    revalidating, so edits to JS/CSS don't show up on a normal reload and
    require a manual hard-refresh. `no-cache` keeps the ETag/304 flow (cheap)
    while guaranteeing changed files are re-fetched.
    """
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/api/health")
async def health(request: Request) -> dict:
    live = getattr(request.app.state, "live_ctx", None)
    monitor = live.connection_monitor if live is not None else None
    if monitor is None:
        mode = "online"
    elif getattr(monitor, "is_forced", False) or getattr(monitor, "_forced_offline", False):
        mode = "forced_offline"
    elif monitor.current_state() == ConnectionState.online:
        mode = "online"
    elif monitor.current_state() == ConnectionState.disconnected:
        mode = "disconnected"
    else:
        mode = "offline"
    return {"status": "ok", "mode": mode}


register_routers(app)
