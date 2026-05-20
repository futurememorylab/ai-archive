from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from backend.app.context import AppContext
from backend.app.logging_setup import configure_logging
from backend.app.seed import seed_default_template
from backend.app.settings import Settings

SEEDS = Path(__file__).resolve().parents[1] / "seeds"


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = Settings()
    init_external = settings.app_env == "prod" or _real_external_enabled(settings)
    ctx = await AppContext.build(settings, init_external=init_external)
    app.state.ctx = ctx
    seed_path = SEEDS / "default_template.json"
    if seed_path.exists():
        await seed_default_template(ctx.db, seed_path=seed_path)
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


app = FastAPI(title="CatDV Annotator", lifespan=lifespan)

from fastapi.staticfiles import StaticFiles  # noqa: E402

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


from backend.app.routes.templates import router as templates_router

app.include_router(templates_router)

from backend.app.routes.catdv import router as catdv_router

app.include_router(catdv_router)

from backend.app.routes.jobs import router as jobs_router

app.include_router(jobs_router)

from backend.app.routes.review import router as review_router

app.include_router(review_router)

from backend.app.routes.media import router as media_router

app.include_router(media_router)

from backend.app.routes.events import router as events_router

app.include_router(events_router)

from backend.app.routes.connection import router as connection_router

app.include_router(connection_router)

from backend.app.routes.workspaces import router as workspaces_router  # noqa: E402

app.include_router(workspaces_router)

from backend.app.routes.sync import router as sync_router  # noqa: E402

app.include_router(sync_router)

from backend.app.routes.ui import router as ui_router  # noqa: E402

app.include_router(ui_router)

from backend.app.routes.cache import (  # noqa: E402
    api_router as cache_api_router,
    page_router as cache_page_router,
    ui_router as cache_ui_router,
)

app.include_router(cache_api_router)
app.include_router(cache_page_router)
app.include_router(cache_ui_router)

from backend.app.routes.pages import router as pages_router  # noqa: E402

app.include_router(pages_router)
