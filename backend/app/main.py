from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.app.context import AppContext
from backend.app.logging_setup import configure_logging
from backend.app.settings import Settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = Settings()
    init_external = settings.app_env == "prod" or _real_external_enabled(settings)
    ctx = await AppContext.build(settings, init_external=init_external)
    app.state.ctx = ctx
    try:
        yield
    finally:
        await ctx.aclose()


def _real_external_enabled(s: Settings) -> bool:
    """In dev tests we bypass external services. In real dev usage, set
    APP_ENV=dev and ensure CATDV_* / GCP_* env vars point at real systems."""
    return all([
        s.catdv_base_url, s.catdv_username, s.catdv_password,
        s.gcp_project_id, s.gcs_bucket_name,
    ])


app = FastAPI(title="CatDV Annotator", lifespan=lifespan)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


from backend.app.routes.templates import router as templates_router
app.include_router(templates_router)

from backend.app.routes.catdv import router as catdv_router
app.include_router(catdv_router)

from backend.app.routes.jobs import router as jobs_router
app.include_router(jobs_router)
