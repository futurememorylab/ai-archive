"""AppContext composition root — owns the DB connection, repos, services,
and the archive/ai-store/proxy stack. Built once at FastAPI startup and
stashed on `app.state` so routes can pull it via `get_ctx`."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import aiosqlite

if TYPE_CHECKING:
    from backend.app.services.catdv_client import CatdvClient
    from backend.app.services.gcs import GcsService
    from backend.app.services.gemini import GeminiService
    from backend.app.services.proxy_resolver import ProxyResolver

from backend.app.archive.ai_store import AIInputStore
from backend.app.archive.ai_stores.registry import build_ai_input_store
from backend.app.archive.provider import ArchiveProvider
from backend.app.archive.registry import build_archive_provider
from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.ai_store_files import AIStoreFilesRepo
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.cache_actions_log import CacheActionsLogRepo
from backend.app.repositories.clip_cache import ClipCacheRepo
from backend.app.repositories.clip_list_cache import ClipListCacheRepo
from backend.app.repositories.field_def_cache import FieldDefCacheRepo
from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.pending_operations import PendingOperationsRepo
from backend.app.repositories.prefetch_queue import PrefetchQueueRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.repositories.proxy_cache import ProxyCacheRepo
from backend.app.repositories.review_items import ReviewItemsRepo
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

    _running_jobs: dict[int, object] = field(default_factory=dict)

    catdv: CatdvClient | None = None
    archive: ArchiveProvider | None = None
    ai_store: AIInputStore | None = None
    gemini: GeminiService | None = None
    proxy_resolver: ProxyResolver | None = None
    # low-level GcsService kept only as a wiring detail
    _gcs_service: GcsService | None = None
    write_queue: WriteQueue | None = None
    sync_engine: SyncEngine | None = None
    connection_monitor: ConnectionMonitor | None = None
    workspace_manager: WorkspaceManager | None = None
    cache_inspector: CacheInspector | None = None
    cache_actions: CacheActions | None = None
    lru_eviction: LruEviction | None = None
    media_prefetcher: MediaPrefetcher | None = None

    @classmethod
    async def build(cls, settings: Settings, *, init_external: bool = True) -> AppContext:
        # Build proceeds top-down through four subsystem builders. Each one
        # mutates the passed-in ctx; many also install closures over ctx so
        # they can defer-read fields populated by *later* builders (e.g.
        # ConnectionMonitor's is_online_provider reads ctx.connection_monitor
        # which the same builder assigns moments later). Keep the same ctx
        # threaded through so those closures stay valid — see ARCHITECTURE.md.
        ctx = await _build_core(settings)
        await _build_cache_subsystem(ctx)
        if init_external:
            online_flags = await _build_archive_subsystem(ctx)
            await _build_sync_subsystem(ctx, online_flags)
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


class _OnlineFlags(NamedTuple):
    """Boot-time online/offline determination passed from archive to sync builder.

    ``forced_offline`` is set by ``CATDV_OFFLINE=true``; ``login_failed`` is
    set when the initial CatdvClient.login() round-trip raised. Either flag
    causes ConnectionMonitor to start in the offline state.
    """

    forced_offline: bool
    login_failed: bool


async def _build_core(settings: Settings) -> AppContext:
    """Open the DB, run migrations, recover crashed write rows, instantiate ctx.

    Also wires WriteQueue (no external deps, always available).
    """
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db_path = settings.data_dir / "app.db"
    cm = open_db(db_path)
    conn = await cm.__aenter__()
    await apply_migrations(conn, MIGRATIONS)
    # Crash recovery: any rows left mid-flight from a previous process
    # become pending again. Idempotent; runs every startup.
    await conn.execute(
        "UPDATE pending_operations SET status='pending', attempted_at=NULL WHERE status='in_flight'"
    )
    await conn.commit()

    ctx = AppContext(settings=settings, db=conn, db_cm=cm)

    # WriteQueue has no external deps; always available.
    ctx.write_queue = WriteQueue(
        pending_ops_repo=ctx.pending_ops_repo,
        review_items_repo=ctx.review_items_repo,
    )
    return ctx


async def _build_cache_subsystem(ctx: AppContext) -> None:
    """Reconcile on-disk proxy cache and wire CacheInspector/Actions.

    These are pure-DB services with no external dependencies, so we wire them
    up even when init_external=False. When init_external runs, the archive
    subsystem calls `attach_provider` / `attach_ai_store` on the *same*
    instances so they pick up provider (for deep-orphan checks) and ai_store
    (for bucket-side evictions). The instances themselves are never replaced
    — see PR H / ADR 0021.
    """
    settings = ctx.settings

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

    # CacheInspector + CacheActions are pure-DB; always wire them now and
    # mutate to attach the provider/ai_store later. `ctx.cache_inspector`
    # and `ctx.cache_actions` are bound exactly once for the ctx lifetime.
    cap_bytes = int(settings.media_cache_cap_gb) * 1024**3
    ctx.cache_inspector = CacheInspector(
        db_provider=lambda c=ctx: c.db,
        media_cache_cap_bytes=cap_bytes,
    )
    ctx.cache_actions = CacheActions(
        db_provider=lambda c=ctx: c.db,
        inspector=ctx.cache_inspector,
        log_repo=ctx.cache_actions_log_repo,
        ai_store=None,  # filled in by _build_sync_subsystem via attach_ai_store
    )


async def _build_archive_subsystem(ctx: AppContext) -> _OnlineFlags:
    """Log into CatDV (if configured) and wire archive, AI store, gemini, resolver.

    Returns the boot-time online flags so the sync builder can pass them to
    ConnectionMonitor. Lazy imports here avoid pulling httpx / google libs
    when init_external=False (tests, CLI tools).
    """
    import logging

    from backend.app.services.catdv_client import (
        CatdvAuthError,
        CatdvClient,
    )
    from backend.app.services.gcs import GcsService
    from backend.app.services.gemini import GeminiService
    from backend.app.services.proxy_resolver import build_resolver

    settings = ctx.settings
    use_catdv = settings.archive_provider == "catdv"
    forced_offline = bool(getattr(settings, "catdv_offline", False)) and use_catdv
    login_failed = False

    if use_catdv and not forced_offline:
        ctx.catdv = CatdvClient(
            base_url=settings.catdv_base_url,
            username=settings.catdv_username or "",
            password=settings.catdv_password or "",
        )
        await ctx.catdv.__aenter__()
        # CatdvClient.__aenter__ only opens the httpx pool; auth is
        # lazy. Force one round-trip so an unreachable host or bad
        # credentials degrade us to offline cleanly at startup
        # instead of half-booting and tripping the first request.
        try:
            await ctx.catdv.login()
        except CatdvAuthError as exc:
            logging.getLogger(__name__).warning(
                "CatDV login failed at startup (%s); booting offline",
                exc,
            )
            await ctx.catdv.__aexit__(None, None, None)
            ctx.catdv = None
            login_failed = True
        except Exception as exc:  # noqa: BLE001 — transport / DNS
            logging.getLogger(__name__).warning(
                "CatDV unreachable at startup (%s); booting offline",
                exc,
            )
            await ctx.catdv.__aexit__(None, None, None)
            ctx.catdv = None
            login_failed = True

    # The is_online provider closes over ctx so it can read the
    # monitor's state once the monitor is constructed below.
    def _is_online(c=ctx, forced=forced_offline, failed_at_boot=login_failed):
        if forced or failed_at_boot:
            return False
        if c.connection_monitor is None:
            return True
        from backend.app.services.connection_monitor import ConnectionState

        return c.connection_monitor.current_state() == ConnectionState.online

    ctx.archive = build_archive_provider(
        settings,
        catdv_client=ctx.catdv,
        clip_cache_repo=ctx.clip_cache_repo,
        field_def_cache_repo=ctx.field_def_cache_repo,
        clip_list_cache_repo=ctx.clip_list_cache_repo,
        db_provider=lambda c=ctx: c.db,
        is_online_provider=_is_online if use_catdv else None,
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
    if use_catdv and (forced_offline or login_failed):
        ctx.proxy_resolver = build_resolver(
            source="cache-only",
            catdv_client=None,
            cache_dir=settings.data_dir / "cache" / "proxies",
            proxy_cache_repo=ctx.proxy_cache_repo,
            db_provider=lambda c=ctx: c.db,
        )
    elif use_catdv:
        media_store_map = None
        if settings.proxy_source == "filesystem":
            from backend.app.services.media_store_map import (
                fetch_media_store_map,
            )

            media_store_map = await fetch_media_store_map(ctx.catdv)
        ctx.proxy_resolver = build_resolver(
            source=settings.proxy_source,
            catdv_client=ctx.catdv,
            cache_dir=settings.data_dir / "cache" / "proxies",
            archive=ctx.archive,
            media_store_map=media_store_map,
            proxy_cache_repo=ctx.proxy_cache_repo,
            db_provider=lambda c=ctx: c.db,
        )
    else:
        # FS adapter has media_is_local=True; the workspace
        # manager skips the proxy-resolver step entirely.
        ctx.proxy_resolver = None

    return _OnlineFlags(forced_offline=forced_offline, login_failed=login_failed)


async def _build_sync_subsystem(ctx: AppContext, flags: _OnlineFlags) -> None:
    """Wire ConnectionMonitor, SyncEngine, WorkspaceManager, LRU eviction,
    and (if the resolver supports it) MediaPrefetcher.

    The cache_inspector / cache_actions instances are *already* built by
    _build_cache_subsystem; here we only `attach_provider` /
    `attach_ai_store` so deep-orphan and bucket-side eviction code paths
    pick up the now-wired archive provider and AI store. See ADR 0021.
    """
    from backend.app.services.connection_monitor import ConnectionState
    from backend.app.services.proxy_resolver import LocalCacheOnlyResolver

    settings = ctx.settings
    cap_bytes = int(settings.media_cache_cap_gb) * 1024**3

    # archive is guaranteed populated by _build_archive_subsystem.
    assert ctx.archive is not None
    # cache services were wired by _build_cache_subsystem; we only mutate
    # them here, never re-bind ctx.cache_inspector / ctx.cache_actions.
    assert ctx.cache_inspector is not None
    assert ctx.cache_actions is not None

    ctx.connection_monitor = ConnectionMonitor(
        provider=ctx.archive,
        db_provider=lambda c=ctx: c.db,
        interval_s=float(settings.health_probe_interval_s),
        timeout_s=float(settings.health_probe_timeout_s),
        event_bus=ctx.event_bus,
        forced_offline=flags.forced_offline,
        initial_state=(ConnectionState.offline if flags.login_failed else ConnectionState.online),
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
    # Attach the late-bound deps to the cache services. The instances
    # themselves were created in _build_cache_subsystem and stay the
    # same object identity for the ctx's lifetime — this is the
    # acceptance criterion for PR H / ADR 0021.
    ctx.cache_inspector.attach_provider(
        ctx.archive,
        host_local_proxies=getattr(ctx.proxy_resolver, "is_host_local", False),
    )
    ctx.cache_actions.attach_ai_store(ctx.ai_store)
    ctx.lru_eviction = LruEviction(
        actions=ctx.cache_actions,
        log_repo=ctx.cache_actions_log_repo,
        db_provider=lambda c=ctx: c.db,
        media_cache_cap_bytes=cap_bytes,
        tick_interval_s=float(settings.lru_tick_interval_s),
    )
    if ctx.proxy_resolver is not None and not isinstance(
        ctx.proxy_resolver, LocalCacheOnlyResolver
    ):
        ctx.media_prefetcher = MediaPrefetcher(
            queue_repo=ctx.prefetch_queue_repo,
            resolver=ctx.proxy_resolver,
            db_provider=lambda c=ctx: c.db,
            tick_interval_s=float(settings.prefetch_tick_interval_s),
        )
