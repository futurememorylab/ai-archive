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
from backend.app.repositories.cache_actions_log import CacheActionsLogRepo
from backend.app.repositories.clip_cache import ClipCacheRepo
from backend.app.repositories.clip_list_cache import ClipListCacheRepo
from backend.app.repositories.field_def_cache import FieldDefCacheRepo
from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.pending_operations import PendingOperationsRepo
from backend.app.repositories.prefetch_queue import PrefetchQueueRepo
from backend.app.repositories.proxy_cache import ProxyCacheRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.repositories.workspaces import WorkspacesRepo
from backend.app.repositories.write_log import WriteLogRepo
from backend.app.services.cache_actions import CacheActions
from backend.app.services.cache_inspector import CacheInspector
from backend.app.services.connection_monitor import ConnectionMonitor
from backend.app.services.events import EventBus
from backend.app.services.lru_eviction import LruEviction
from backend.app.services.media_prefetcher import MediaPrefetcher
from backend.app.services.proxy_cache_reconciler import ProxyCacheReconciler
from backend.app.services.sync_engine import SyncEngine
from backend.app.services.workspace_manager import WorkspaceManager
from backend.app.services.write_queue import WriteQueue
from backend.app.settings import Settings

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


@dataclass
class AppContext:
    settings: Settings
    db: aiosqlite.Connection
    db_cm: object

    prompts_repo: PromptsRepo = field(default_factory=PromptsRepo)
    jobs_repo: JobsRepo = field(default_factory=JobsRepo)
    annotations_repo: AnnotationsRepo = field(default_factory=AnnotationsRepo)
    review_items_repo: ReviewItemsRepo = field(default_factory=ReviewItemsRepo)
    write_log_repo: WriteLogRepo = field(default_factory=WriteLogRepo)
    proxy_cache_repo: ProxyCacheRepo = field(default_factory=ProxyCacheRepo)
    ai_store_files_repo: AIStoreFilesRepo = field(default_factory=AIStoreFilesRepo)
    clip_cache_repo: ClipCacheRepo = field(default_factory=ClipCacheRepo)
    clip_list_cache_repo: ClipListCacheRepo = field(default_factory=ClipListCacheRepo)
    field_def_cache_repo: FieldDefCacheRepo = field(default_factory=FieldDefCacheRepo)
    pending_ops_repo: PendingOperationsRepo = field(default_factory=PendingOperationsRepo)
    workspaces_repo: WorkspacesRepo = field(default_factory=WorkspacesRepo)
    cache_actions_log_repo: CacheActionsLogRepo = field(default_factory=CacheActionsLogRepo)
    prefetch_queue_repo: PrefetchQueueRepo = field(default_factory=PrefetchQueueRepo)
    event_bus: EventBus = field(default_factory=EventBus)

    _running_jobs: dict[int, "object"] = field(default_factory=dict)

    catdv = None
    archive: ArchiveProvider | None = None
    ai_store: AIInputStore | None = None
    gemini = None
    proxy_resolver = None
    _gcs_service = None   # low-level GcsService kept only as a wiring detail
    write_queue: WriteQueue | None = None
    sync_engine: SyncEngine | None = None
    connection_monitor: ConnectionMonitor | None = None
    workspace_manager: WorkspaceManager | None = None
    cache_inspector: CacheInspector | None = None
    cache_actions: CacheActions | None = None
    lru_eviction: LruEviction | None = None
    media_prefetcher: MediaPrefetcher | None = None

    @classmethod
    async def build(cls, settings: Settings, *, init_external: bool = True) -> "AppContext":
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        db_path = settings.data_dir / "app.db"
        cm = open_db(db_path)
        conn = await cm.__aenter__()
        await apply_migrations(conn, MIGRATIONS)
        # Crash recovery: any rows left mid-flight from a previous process
        # become pending again. Idempotent; runs every startup.
        await conn.execute(
            "UPDATE pending_operations "
            "SET status='pending', attempted_at=NULL "
            "WHERE status='in_flight'"
        )
        await conn.commit()

        ctx = cls(settings=settings, db=conn, db_cm=cm)

        # Reconcile the proxy cache against on-disk files. Cheap, idempotent,
        # touches local disk + SQLite only — safe to run before init_external.
        # Keeps the cache view honest about what's actually on disk on every
        # restart (handles files left from older builds or manual downloads).
        cache_dir = settings.data_dir / "cache" / "proxies"
        reconciler = ProxyCacheReconciler(
            cache_dir=cache_dir,
            proxy_cache_repo=ctx.proxy_cache_repo,
            db_provider=lambda c=ctx: c.db,
        )
        await reconciler.reconcile()

        # WriteQueue has no external deps; always available.
        ctx.write_queue = WriteQueue(
            pending_ops_repo=ctx.pending_ops_repo,
            review_items_repo=ctx.review_items_repo,
        )

        # CacheInspector + CacheActions are pure-DB; always wire them.
        cap_bytes = int(settings.media_cache_cap_gb) * 1024 ** 3
        ctx.cache_inspector = CacheInspector(
            db_provider=lambda c=ctx: c.db,
            media_cache_cap_bytes=cap_bytes,
        )
        ctx.cache_actions = CacheActions(
            db_provider=lambda c=ctx: c.db,
            inspector=ctx.cache_inspector,
            log_repo=ctx.cache_actions_log_repo,
            ai_store=None,  # filled in when init_external runs
        )

        if init_external:
            from backend.app.services.catdv_client import CatdvClient
            from backend.app.services.gcs import GcsService
            from backend.app.services.gemini import GeminiService
            from backend.app.services.proxy_resolver import build_resolver

            use_catdv = settings.archive_provider == "catdv"
            if use_catdv:
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
                clip_list_cache_repo=ctx.clip_list_cache_repo,
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
            if use_catdv:
                ctx.proxy_resolver = build_resolver(
                    source=settings.proxy_source,
                    catdv_client=ctx.catdv,
                    cache_dir=settings.data_dir / "cache" / "proxies",
                    fs_root=settings.proxy_fs_root,
                    path_template=settings.proxy_path_template,
                    proxy_cache_repo=ctx.proxy_cache_repo,
                    db_provider=lambda c=ctx: c.db,
                )
            else:
                # FS adapter has media_is_local=True; the workspace
                # manager skips the proxy-resolver step entirely.
                ctx.proxy_resolver = None
            ctx.connection_monitor = ConnectionMonitor(
                provider=ctx.archive,
                db_provider=lambda c=ctx: c.db,
                interval_s=float(settings.health_probe_interval_s),
                timeout_s=float(settings.health_probe_timeout_s),
                event_bus=ctx.event_bus,
            )
            ctx.sync_engine = SyncEngine(
                provider=ctx.archive,
                pending_ops_repo=ctx.pending_ops_repo,
                write_log_repo=ctx.write_log_repo,
                connection_monitor=ctx.connection_monitor,
                db_provider=lambda c=ctx: c.db,
                event_bus=ctx.event_bus,
                tick_interval_s=float(settings.sync_tick_interval_s),
                retry_base_s=float(settings.sync_retry_base_s),
                retry_max_s=float(settings.sync_retry_max_s),
            )
            ctx.workspace_manager = WorkspaceManager(
                workspaces_repo=ctx.workspaces_repo,
                provider=ctx.archive,
                proxy_resolver=ctx.proxy_resolver,
                db_provider=lambda c=ctx: c.db,
            )
            # Inspector picks up the provider for deep-orphan checks;
            # actions pick up the AI store for bucket-side evictions.
            ctx.cache_inspector = CacheInspector(
                db_provider=lambda c=ctx: c.db,
                media_cache_cap_bytes=cap_bytes,
                provider=ctx.archive,
            )
            ctx.cache_actions = CacheActions(
                db_provider=lambda c=ctx: c.db,
                inspector=ctx.cache_inspector,
                log_repo=ctx.cache_actions_log_repo,
                ai_store=ctx.ai_store,
            )
            ctx.lru_eviction = LruEviction(
                actions=ctx.cache_actions,
                log_repo=ctx.cache_actions_log_repo,
                db_provider=lambda c=ctx: c.db,
                media_cache_cap_bytes=cap_bytes,
                tick_interval_s=float(settings.lru_tick_interval_s),
            )
            if ctx.proxy_resolver is not None:
                ctx.media_prefetcher = MediaPrefetcher(
                    queue_repo=ctx.prefetch_queue_repo,
                    resolver=ctx.proxy_resolver,
                    db_provider=lambda c=ctx: c.db,
                    tick_interval_s=float(settings.prefetch_tick_interval_s),
                )
        return ctx

    async def aclose(self) -> None:
        if self.media_prefetcher is not None:
            await self.media_prefetcher.stop()
        if self.lru_eviction is not None:
            await self.lru_eviction.stop()
        if self.sync_engine is not None:
            await self.sync_engine.stop()
        if self.connection_monitor is not None:
            await self.connection_monitor.stop()
        if self.catdv is not None:
            await self.catdv.__aexit__(None, None, None)
        await self.db_cm.__aexit__(None, None, None)
