"""FastAPI app factory and lifespan. Wires routers, mounts static assets,
and owns the `AppContext` lifecycle (build at startup, aclose at shutdown
to release the CatDV session seat)."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from backend.app.context import AppContext
from backend.app.logging_setup import configure_logging
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
from backend.app.routes import studio as studio_route_module
from backend.app.routes.review import router as review_router
from backend.app.routes.sync import router as sync_router
from backend.app.routes.ui import router as ui_router
from backend.app.routes.workspaces import router as workspaces_router
from backend.app.seed import seed_default_prompt, seed_live_system_instruction
from backend.app.services.connection_monitor import ConnectionState
from backend.app.settings import Settings
from backend.app.startup import run_startup_cleanup

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
    init_external = settings.app_env == "prod" or _real_external_enabled(settings)
    ctx = await AppContext.build(settings, init_external=init_external)
    app.state.ctx = ctx
    seed_path = SEEDS / "default_template.json"
    if seed_path.exists():
        await seed_default_prompt(ctx.db, seed_path=seed_path)
    live_seed = SEEDS / "live_system_instruction_cs.json"
    if live_seed.exists():
        await seed_live_system_instruction(ctx.db, seed_path=live_seed)
    await run_startup_cleanup(ctx.db)
    if init_external:
        if ctx.connection_monitor is not None:
            await ctx.connection_monitor.start()
        if ctx.sync_engine is not None:
            await ctx.sync_engine.start()
        if ctx.lru_eviction is not None:
            await ctx.lru_eviction.start()
        if ctx.media_prefetcher is not None:
            await ctx.media_prefetcher.start()
    try:
        yield
    finally:
        await ctx.aclose()


def register_routers(app: FastAPI) -> None:
    app.include_router(prompts_router)
    app.include_router(catdv_router)
    app.include_router(jobs_router)
    app.include_router(review_router)
    app.include_router(media_router)
    app.include_router(events_router)
    app.include_router(connection_router)
    app.include_router(workspaces_router)
    app.include_router(sync_router)
    app.include_router(ui_router)
    app.include_router(cache_api_router)
    app.include_router(cache_page_router)
    app.include_router(cache_ui_router)
    for r in page_routers:
        app.include_router(r)
    app.include_router(live_router)
    app.include_router(studio_route_module.router)


app = FastAPI(title="CatDV Annotator", lifespan=lifespan)
_STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/api/health")
async def health(request: Request) -> dict:
    ctx = getattr(request.app.state, "ctx", None)
    monitor = getattr(ctx, "connection_monitor", None) if ctx else None
    if monitor is None:
        mode = "online"
    elif getattr(monitor, "is_forced", False) or getattr(monitor, "_forced_offline", False):
        mode = "forced_offline"
    else:
        mode = "online" if monitor.current_state() == ConnectionState.online else "offline"
    return {"status": "ok", "mode": mode}


register_routers(app)
