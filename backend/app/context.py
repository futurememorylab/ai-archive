from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import aiosqlite

from backend.app.archive.provider import ArchiveProvider
from backend.app.archive.registry import build_archive_provider
from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.archive.ai_store import AIInputStore
from backend.app.archive.ai_stores.registry import build_ai_input_store
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.ai_store_files import AIStoreFilesRepo
from backend.app.repositories.clip_cache import ClipCacheRepo
from backend.app.repositories.field_def_cache import FieldDefCacheRepo
from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.proxy_cache import ProxyCacheRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from backend.app.repositories.templates import TemplatesRepo
from backend.app.repositories.write_log import WriteLogRepo
from backend.app.services.events import EventBus
from backend.app.settings import Settings

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


@dataclass
class AppContext:
    settings: Settings
    db: aiosqlite.Connection
    db_cm: object

    templates_repo: TemplatesRepo = field(default_factory=TemplatesRepo)
    jobs_repo: JobsRepo = field(default_factory=JobsRepo)
    annotations_repo: AnnotationsRepo = field(default_factory=AnnotationsRepo)
    review_items_repo: ReviewItemsRepo = field(default_factory=ReviewItemsRepo)
    write_log_repo: WriteLogRepo = field(default_factory=WriteLogRepo)
    proxy_cache_repo: ProxyCacheRepo = field(default_factory=ProxyCacheRepo)
    ai_store_files_repo: AIStoreFilesRepo = field(default_factory=AIStoreFilesRepo)
    clip_cache_repo: ClipCacheRepo = field(default_factory=ClipCacheRepo)
    field_def_cache_repo: FieldDefCacheRepo = field(default_factory=FieldDefCacheRepo)
    event_bus: EventBus = field(default_factory=EventBus)

    _running_jobs: dict[int, "object"] = field(default_factory=dict)

    catdv = None
    archive: ArchiveProvider | None = None
    ai_store: AIInputStore | None = None
    gemini = None
    proxy_resolver = None
    _gcs_service = None   # low-level GcsService kept only as a wiring detail

    @classmethod
    async def build(cls, settings: Settings, *, init_external: bool = True) -> "AppContext":
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        db_path = settings.data_dir / "app.db"
        cm = open_db(db_path)
        conn = await cm.__aenter__()
        await apply_migrations(conn, MIGRATIONS)

        ctx = cls(settings=settings, db=conn, db_cm=cm)

        if init_external:
            from backend.app.services.catdv_client import CatdvClient
            from backend.app.services.gcs import GcsService
            from backend.app.services.gemini import GeminiService
            from backend.app.services.proxy_resolver import build_resolver

            ctx.catdv = CatdvClient(
                base_url=settings.catdv_base_url,
                username=settings.catdv_username or "",
                password=settings.catdv_password or "",
            )
            await ctx.catdv.__aenter__()
            ctx.archive = build_archive_provider(
                settings,
                catdv_client=ctx.catdv,
                clip_cache_repo=ctx.clip_cache_repo,
                field_def_cache_repo=ctx.field_def_cache_repo,
                db_provider=lambda c=ctx: c.db,
            )
            ctx._gcs_service = GcsService(settings.gcs_bucket_name)
            ctx.ai_store = build_ai_input_store(
                settings,
                gcs_service=ctx._gcs_service,
                files_repo=ctx.ai_store_files_repo,
                db_provider=lambda c=ctx: c.db,
            )
            ctx.gemini = GeminiService(
                project=settings.gcp_project_id,
                location=settings.gcp_location,
            )
            ctx.proxy_resolver = build_resolver(
                source=settings.proxy_source,
                catdv_client=ctx.catdv,
                cache_dir=settings.data_dir / "cache" / "proxies",
                fs_root=settings.proxy_fs_root,
                path_template=settings.proxy_path_template,
            )
        return ctx

    async def aclose(self) -> None:
        if self.catdv is not None:
            await self.catdv.__aexit__(None, None, None)
        await self.db_cm.__aexit__(None, None, None)
