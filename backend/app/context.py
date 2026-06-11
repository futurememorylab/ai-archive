"""Composition root, split into two dataclasses by lifetime.

``CoreCtx`` owns everything that is **always present**, even when
``init_external=False`` (offline boot, CLI tools, tests): the DB
connection, every repository, the write queue, the event bus, and the
two DB-first cache services (``cache_inspector`` / ``cache_actions``).

``LiveCtx`` *composes* a ``CoreCtx`` (it carries a ``core`` field, not
inheritance) and adds the genuinely-external services that only exist
when ``init_external=True``: the archive provider, the AI input store,
Gemini, the proxy resolver, the connection monitor, the sync engine,
the workspace manager, LRU eviction and the media prefetcher. The
type system therefore carries the offline/online contract — live-only
routes depend on ``get_live_ctx`` and get a typed 503 when offline,
instead of scattered ``assert ctx.foo is not None``.

``LiveCtx`` exposes every ``CoreCtx`` field via a thin typed property
delegator, so handlers that touch both core and live fields read them
off one object with no per-field rewrite.

Build returns a ``CoreCtx`` always and a ``LiveCtx | None`` (None when
``init_external=False``). The lifespan stashes both on ``app.state``."""

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
    from backend.app.services.thumbnail_service import ThumbnailService

from backend.app.archive.ai_store import AIInputStore
from backend.app.archive.ai_stores.registry import build_ai_input_store
from backend.app.archive.provider import ArchiveProvider
from backend.app.archive.registry import build_archive_provider
from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.models.telemetry import TelemetryCtx
from backend.app.repositories.ai_store_files import AIStoreFilesRepo
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.app_meta import get_or_create_install_id
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
from backend.app.repositories.run_telemetry import RunTelemetryRepo
from backend.app.repositories.studio_runs import StudioRunsRepo
from backend.app.repositories.studio_sets import StudioSetsRepo
from backend.app.repositories.uploaded_clips import UploadedClipsRepo
from backend.app.repositories.workspaces import WorkspacesRepo
from backend.app.repositories.write_log import WriteLogRepo
from backend.app.services.cache_actions import CacheActions
from backend.app.services.cache_inspector import CacheInspector
from backend.app.services.connection_monitor import ConnectionMonitor
from backend.app.services.events import EventBus
from backend.app.services.idle_disconnector import IdleDisconnector
from backend.app.services.lru_eviction import LruEviction
from backend.app.services.media_cache import MediaCacheBackend, build_media_cache_backend
from backend.app.services.media_prefetcher import MediaPrefetcher
from backend.app.services.proxy_cache_reconciler import ProxyCacheReconciler
from backend.app.services.sync_engine import SyncEngine
from backend.app.services.workspace_manager import WorkspaceManager
from backend.app.services.write_queue import WriteQueue
from backend.app.settings import Settings

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


@dataclass
class CoreCtx:
    """Always-present state. No Optional service fields."""

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
    studio_sets_repo: StudioSetsRepo = field(default_factory=StudioSetsRepo)
    studio_runs_repo: StudioRunsRepo = field(default_factory=StudioRunsRepo)
    uploaded_clips_repo: UploadedClipsRepo = field(default_factory=UploadedClipsRepo)
    run_telemetry_repo: RunTelemetryRepo = field(default_factory=RunTelemetryRepo)
    telemetry_ctx: TelemetryCtx = field(init=False)
    event_bus: EventBus = field(default_factory=EventBus)

    _running_jobs: dict[int, object] = field(default_factory=dict)

    write_queue: WriteQueue = field(init=False)
    # Cache services are DB-first (offline-required). Their live
    # augmentations (deep-orphan provider checks, bucket-side AI
    # eviction) are each a single None-guarded call site, so they live
    # on CoreCtx and are built with possibly-None live deps.
    cache_inspector: CacheInspector = field(init=False)
    cache_actions: CacheActions = field(init=False)

    @classmethod
    async def build(cls, settings: Settings) -> CoreCtx:
        """Open the DB, run migrations, recover crashed write rows.

        Builds the WriteQueue (no external deps). Cache services are
        wired separately, *after* the archive subsystem, so they can
        receive the (possibly-None) provider / ai_store directly — see
        ``build_context``.
        """
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        db_path = settings.data_dir / "app.db"
        cm = open_db(db_path)
        conn = await cm.__aenter__()
        await apply_migrations(conn, MIGRATIONS)
        # Crash recovery: any rows left mid-flight from a previous process
        # become pending again. Idempotent; runs every startup.
        await conn.execute(
            "UPDATE pending_operations SET status='pending', attempted_at=NULL "
            "WHERE status='in_flight'"
        )
        await conn.commit()

        ctx = cls(settings=settings, db=conn, db_cm=cm)
        # WriteQueue has no external deps; always available.
        ctx.write_queue = WriteQueue(
            pending_ops_repo=ctx.pending_ops_repo,
            review_items_repo=ctx.review_items_repo,
        )

        import os
        from urllib.parse import urlparse

        install_id = await get_or_create_install_id(conn)
        host = urlparse(settings.catdv_base_url).netloc or None
        archive_id = (
            f"{settings.archive_provider}:{host}"
            if settings.archive_provider == "catdv" and host
            else settings.archive_provider
        )
        ctx.telemetry_ctx = TelemetryCtx(
            install_id=install_id,
            app_version=os.environ.get("APP_VERSION"),
            archive_id=archive_id,
            vertex_project=settings.gcp_project_id,
            vertex_location=settings.gcp_location,
        )
        return ctx

    def _wire_cache_services(
        self,
        *,
        provider: ArchiveProvider | None,
        ai_store: AIInputStore | None,
        host_local_proxies: bool,
    ) -> None:
        """Build cache_inspector / cache_actions with their (possibly-None)
        live deps passed directly to the constructors. Called once during
        build, after the archive subsystem (if any) is wired."""
        cap_bytes = int(self.settings.media_cache_cap_gb) * 1024**3
        self.cache_inspector = CacheInspector(
            db_provider=lambda: self.db,
            media_cache_cap_bytes=cap_bytes,
            provider=provider,
            host_local_proxies=host_local_proxies,
        )
        self.cache_actions = CacheActions(
            db_provider=lambda: self.db,
            inspector=self.cache_inspector,
            log_repo=self.cache_actions_log_repo,
            ai_store=ai_store,
        )

    async def aclose(self) -> None:
        await self.db_cm.__aexit__(None, None, None)  # type: ignore[attr-defined]


@dataclass
class LiveCtx:
    """Composes a CoreCtx and adds the genuinely-external services.

    ``archive`` / ``ai_store`` / ``gemini`` are always built when
    ``init_external=True`` (non-Optional). ``catdv`` is legitimately
    ``CatdvClient | None`` — the app can boot "live" with CatDV offline
    (forced offline, auth failure, seat limit). ``proxy_resolver`` is
    None in fs mode, ``thumbnail_service`` is built only for CatDV, and
    ``media_prefetcher`` is None for a cache-only resolver.
    """

    core: CoreCtx

    archive: ArchiveProvider
    ai_store: AIInputStore
    gemini: GeminiService
    sync_engine: SyncEngine
    connection_monitor: ConnectionMonitor
    workspace_manager: WorkspaceManager
    lru_eviction: LruEviction
    _gcs_service: GcsService

    catdv: CatdvClient | None = None
    proxy_resolver: ProxyResolver | None = None
    thumbnail_service: ThumbnailService | None = None
    media_cache_backend: MediaCacheBackend | None = None
    media_prefetcher: MediaPrefetcher | None = None
    idle_disconnector: IdleDisconnector | None = None

    # --- thin delegators to the composed CoreCtx -------------------
    # Live-route handlers touch both core fields (db, repos) and live
    # fields (archive) off one object; these keep the per-handler diff
    # to just the accessor name.

    @property
    def settings(self) -> Settings:
        return self.core.settings

    @property
    def db(self) -> aiosqlite.Connection:
        return self.core.db

    @property
    def db_cm(self) -> object:
        return self.core.db_cm

    @property
    def prompts_repo(self) -> PromptsRepo:
        return self.core.prompts_repo

    @property
    def jobs_repo(self) -> JobsRepo:
        return self.core.jobs_repo

    @property
    def annotations_repo(self) -> AnnotationsRepo:
        return self.core.annotations_repo

    @property
    def review_items_repo(self) -> ReviewItemsRepo:
        return self.core.review_items_repo

    @property
    def write_log_repo(self) -> WriteLogRepo:
        return self.core.write_log_repo

    @property
    def proxy_cache_repo(self) -> ProxyCacheRepo:
        return self.core.proxy_cache_repo

    @property
    def ai_store_files_repo(self) -> AIStoreFilesRepo:
        return self.core.ai_store_files_repo

    @property
    def clip_cache_repo(self) -> ClipCacheRepo:
        return self.core.clip_cache_repo

    @property
    def clip_list_cache_repo(self) -> ClipListCacheRepo:
        return self.core.clip_list_cache_repo

    @property
    def field_def_cache_repo(self) -> FieldDefCacheRepo:
        return self.core.field_def_cache_repo

    @property
    def pending_ops_repo(self) -> PendingOperationsRepo:
        return self.core.pending_ops_repo

    @property
    def workspaces_repo(self) -> WorkspacesRepo:
        return self.core.workspaces_repo

    @property
    def cache_actions_log_repo(self) -> CacheActionsLogRepo:
        return self.core.cache_actions_log_repo

    @property
    def prefetch_queue_repo(self) -> PrefetchQueueRepo:
        return self.core.prefetch_queue_repo

    @property
    def studio_sets_repo(self) -> StudioSetsRepo:
        return self.core.studio_sets_repo

    @property
    def studio_runs_repo(self) -> StudioRunsRepo:
        return self.core.studio_runs_repo

    @property
    def uploaded_clips_repo(self) -> UploadedClipsRepo:
        return self.core.uploaded_clips_repo

    @property
    def run_telemetry_repo(self) -> RunTelemetryRepo:
        return self.core.run_telemetry_repo

    @property
    def telemetry_ctx(self) -> TelemetryCtx:
        return self.core.telemetry_ctx

    @property
    def event_bus(self) -> EventBus:
        return self.core.event_bus

    @property
    def write_queue(self) -> WriteQueue:
        return self.core.write_queue

    @property
    def cache_inspector(self) -> CacheInspector:
        return self.core.cache_inspector

    @property
    def cache_actions(self) -> CacheActions:
        return self.core.cache_actions

    @property
    def _running_jobs(self) -> dict[int, object]:
        return self.core._running_jobs

    async def aclose(self) -> None:
        # Stop the live services in the documented order, then close the
        # core (DB). Order matches the pre-split teardown exactly.
        if self.media_prefetcher is not None:
            await self.media_prefetcher.stop()
        if self.lru_eviction is not None:
            await self.lru_eviction.stop()
        if self.sync_engine is not None:
            await self.sync_engine.stop()
        if self.idle_disconnector is not None:
            await self.idle_disconnector.stop()
        if self.connection_monitor is not None:
            await self.connection_monitor.stop()
        if self.catdv is not None:
            await self.catdv.__aexit__(None, None, None)
        await self.core.aclose()


class _OnlineFlags(NamedTuple):
    """Boot-time online/offline determination passed from archive to sync builder.

    ``forced_offline`` is set by ``CATDV_OFFLINE=true``; ``login_failed`` is
    set when the initial CatdvClient.login() round-trip raised. Either flag
    causes ConnectionMonitor to start in the offline state.
    """

    forced_offline: bool
    login_failed: bool
    manual: bool = False


class _ArchiveSubsystem(NamedTuple):
    """Everything the archive builder produces, threaded to the sync builder."""

    archive: ArchiveProvider
    ai_store: AIInputStore
    gemini: GeminiService
    gcs_service: GcsService
    catdv: CatdvClient | None
    proxy_resolver: ProxyResolver | None
    thumbnail_service: ThumbnailService | None
    flags: _OnlineFlags


async def build_context(
    settings: Settings, *, init_external: bool = True
) -> tuple[CoreCtx, LiveCtx | None]:
    """Composition root. Always returns a CoreCtx; returns a LiveCtx too
    when ``init_external`` is True.

    Build order:
      1. core (repos, write_queue)
      2. if init_external: archive subsystem (archive, ai_store, gemini,
         proxy_resolver, thumbnail, gcs) — installs the is_online closure
         that defer-reads the connection monitor built in step 4.
      3. wire cache services with the (possibly-None) provider / ai_store.
      4. if init_external: sync subsystem (connection_monitor, sync_engine,
         workspace_manager, lru_eviction, media_prefetcher).
      5. if init_external: assemble LiveCtx(core=core, ...).
    """
    core = await CoreCtx.build(settings)
    await _reconcile_proxy_cache(core)

    if not init_external:
        core._wire_cache_services(provider=None, ai_store=None, host_local_proxies=False)
        return core, None

    # The is_online closure (built inside the archive subsystem) reads the
    # connection monitor that the sync subsystem assigns moments later. We
    # thread a mutable holder so the closure can find the monitor once it
    # exists — preserving the previous defer-read behaviour exactly.
    monitor_holder: dict[str, ConnectionMonitor | None] = {"monitor": None}
    arch = await _build_archive_subsystem(core, monitor_holder)
    core._wire_cache_services(
        provider=arch.archive,
        ai_store=arch.ai_store,
        host_local_proxies=getattr(arch.proxy_resolver, "is_host_local", False),
    )
    live = await _build_sync_subsystem(core, arch, monitor_holder)
    return core, live


async def _reconcile_proxy_cache(core: CoreCtx) -> None:
    """Reconcile the on-disk proxy cache against the DB index.

    Cheap, idempotent, touches local disk + SQLite only — safe to run
    before init_external. Keeps the cache view honest about what's
    actually on disk on every restart.
    """
    settings = core.settings
    cache_dir = settings.data_dir / "cache" / "proxies"
    reconciler = ProxyCacheReconciler(
        cache_dir=cache_dir,
        proxy_cache_repo=core.proxy_cache_repo,
        db_provider=lambda: core.db,
    )
    await reconciler.reconcile()


async def _build_archive_subsystem(
    core: CoreCtx,
    monitor_holder: dict[str, ConnectionMonitor | None],
) -> _ArchiveSubsystem:
    """Log into CatDV (if configured) and build archive, AI store, gemini,
    resolver, thumbnail.

    Lazy imports here avoid pulling httpx / google libs when
    init_external=False (tests, CLI tools).
    """
    import asyncio
    import logging

    from backend.app.services.catdv_client import (
        CatdvAuthError,
        CatdvBusyError,
        CatdvClient,
    )
    from backend.app.services.gcs import GcsService
    from backend.app.services.gemini import GeminiService
    from backend.app.services.proxy_resolver import build_resolver
    from backend.app.services.thumbnail_service import ThumbnailService

    settings = core.settings
    use_catdv = settings.archive_provider == "catdv"
    forced_offline = bool(getattr(settings, "catdv_offline", False)) and use_catdv
    connect_mode = getattr(settings, "catdv_connect_mode", "manual")
    manual = use_catdv and not forced_offline and connect_mode == "manual"
    login_failed = False
    catdv: CatdvClient | None = None

    # Bridge GOOGLE_APPLICATION_CREDENTIALS from the .env-loaded Settings
    # object back into os.environ before constructing any Google client
    # (GcsService and GeminiService both call google.auth.default()).
    # pydantic-settings parses .env into the Settings instance only; the
    # google-auth SDK reads ADC sources from os.environ directly. Without
    # this bridge, ADC silently falls through to the user's gcloud creds
    # at ~/.config/gcloud/application_default_credentials.json, whose
    # OAuth refresh token expires hourly and then breaks every annotate
    # call with `invalid_grant: Bad Request`.
    if settings.google_application_credentials:
        import os

        os.environ.setdefault(
            "GOOGLE_APPLICATION_CREDENTIALS",
            str(settings.google_application_credentials),
        )

    if use_catdv and not forced_offline:
        catdv = CatdvClient(
            base_url=settings.catdv_base_url,
            username=settings.catdv_username or "",
            password=settings.catdv_password or "",
        )
        await catdv.__aenter__()
        if connect_mode == "auto":
            # CatdvClient.__aenter__ only opens the httpx pool; auth is
            # lazy. Force one round-trip so an unreachable host or bad
            # credentials degrade us to offline cleanly at startup
            # instead of half-booting and tripping the first request.
            #
            # Bound this probe with catdv_startup_login_timeout_s: the client's
            # own 60s timeout is sized for large downloads, but a silently
            # unreachable host (VPN drop, server off) would otherwise stall every
            # restart for that full window. A timeout here is transport-like, so
            # it lands in the generic `except` below — client kept alive, booted
            # offline, recoverable via the Reconnect button.
            try:
                await asyncio.wait_for(
                    catdv.login(),
                    timeout=settings.catdv_startup_login_timeout_s,
                )
            except CatdvAuthError as exc:
                # Bad credentials — retry won't help. Tear down the client
                # so the monitor cannot misread "client present" as
                # "reauthable" later.
                from backend.app.services.errors import humanise

                logging.getLogger(__name__).warning(
                    "CatDV login rejected at startup (%s); booting offline",
                    humanise(exc),
                )
                await catdv.__aexit__(None, None, None)
                catdv = None
                login_failed = True
            except CatdvBusyError as exc:
                # Seat limit reached — recoverable. Keep the client alive so
                # ConnectionMonitor.retry_now() can re-probe once the stale
                # session times out or the admin frees the seat.
                from backend.app.services.errors import humanise

                logging.getLogger(__name__).warning(
                    "CatDV seat limit reached at startup (%s); booting offline — "
                    "click Reconnect once a seat frees up",
                    humanise(exc),
                )
                login_failed = True
            except Exception as exc:  # noqa: BLE001 — transport / DNS / parse
                # Network-side failure. Could be VPN flap, server stop, or a
                # non-JSON 5xx body (the seat-limit web servlet sometimes
                # answers with HTML). Keep the client alive for retry.
                # Route through humanise() so httpx exceptions with empty
                # str() (e.g. ConnectError on VPN down) don't log as "()".
                from backend.app.services.errors import humanise

                logging.getLogger(__name__).warning(
                    "CatDV unreachable at startup (%s); booting offline — "
                    "click Reconnect once the server is reachable",
                    humanise(exc),
                )
                login_failed = True
        # Manual mode: client is built but stays logged out until the
        # operator clicks Connect (POST /api/connection/connect).

    # The is_online provider reads the monitor's state once the sync
    # subsystem constructs it (via the shared holder). We delegate to the
    # monitor instead of latching `login_failed` here, so a successful
    # retry_now() (e.g. after a seat frees up) actually flips the app back
    # to online for routes and services.
    def _is_online(forced=forced_offline):
        if forced:
            return False
        monitor = monitor_holder["monitor"]
        if monitor is None:
            return True
        from backend.app.services.connection_monitor import ConnectionState

        return monitor.current_state() == ConnectionState.online

    archive = build_archive_provider(
        settings,
        catdv_client=catdv,
        clip_cache_repo=core.clip_cache_repo,
        field_def_cache_repo=core.field_def_cache_repo,
        clip_list_cache_repo=core.clip_list_cache_repo,
        db_provider=lambda: core.db,
        is_online_provider=_is_online if use_catdv else None,
    )
    gcs_service = GcsService(settings.gcs_bucket_name)
    ai_store = build_ai_input_store(
        settings,
        gcs_service=gcs_service,
        files_repo=core.ai_store_files_repo,
        db_provider=lambda: core.db,
    )
    gemini = GeminiService(
        project=settings.gcp_project_id,
        location=settings.gcp_location,
    )
    proxy_resolver: ProxyResolver | None
    if use_catdv and (forced_offline or login_failed):
        proxy_resolver = build_resolver(
            source="cache-only",
            catdv_client=None,
            cache_dir=settings.data_dir / "cache" / "proxies",
            proxy_cache_repo=core.proxy_cache_repo,
            db_provider=lambda: core.db,
        )
    elif use_catdv:
        media_store_map = None
        if settings.proxy_source == "filesystem":
            from backend.app.services.media_store_map import (
                fetch_media_store_map,
            )

            media_store_map = await fetch_media_store_map(catdv)
        proxy_resolver = build_resolver(
            source=settings.proxy_source,
            catdv_client=catdv,
            cache_dir=settings.data_dir / "cache" / "proxies",
            archive=archive,
            media_store_map=media_store_map,
            proxy_cache_repo=core.proxy_cache_repo,
            db_provider=lambda: core.db,
        )
    else:
        # FS adapter has media_is_local=True; the workspace
        # manager skips the proxy-resolver step entirely.
        proxy_resolver = None

    # Thumbnail cache: plain JPEG files alongside the proxy cache. Pass the
    # CatDV client only when we actually have one (online or seat-recoverable);
    # in cache-only / fs modes the service still serves already-cached files.
    # is_online_provider gates network fetches: when the connection monitor
    # reports offline, cache misses are terminal (no network attempts).
    thumbnail_service: ThumbnailService | None = None
    if use_catdv:
        # Orphan thumbs on /cache (clips with bytes in proxy_cache /
        # ai_store_files but no clip_cache row) used to stall up to 60 s
        # each on `archive.get_clip()` whenever CatDV was reachable-but-slow
        # or during the 30 s blind spot between failure and the connection
        # monitor flipping to offline. Without a clip_cache row, posterID is
        # unknowable — short-circuit before the network call.
        async def _has_clip_metadata(clip_id: int) -> bool:
            row = await core.clip_cache_repo.get_row(
                core.db,
                provider_id=archive.id,
                provider_clip_id=str(clip_id),
            )
            return row is not None

        durable_thumb_store = None
        if settings.media_cache == "ai_store":
            from backend.app.services.thumbnail_store import GcsThumbnailStore

            durable_thumb_store = GcsThumbnailStore(gcs_service)

        thumbnail_service = ThumbnailService(
            cache_dir=settings.data_dir / "cache" / "thumbs",
            archive=archive,
            catdv=catdv,
            is_online_provider=_is_online,
            metadata_cached_provider=_has_clip_metadata,
            durable_store=durable_thumb_store,
        )

    return _ArchiveSubsystem(
        archive=archive,
        ai_store=ai_store,
        gemini=gemini,
        gcs_service=gcs_service,
        catdv=catdv,
        proxy_resolver=proxy_resolver,
        thumbnail_service=thumbnail_service,
        flags=_OnlineFlags(
            forced_offline=forced_offline, login_failed=login_failed, manual=manual
        ),
    )


async def _build_sync_subsystem(
    core: CoreCtx,
    arch: _ArchiveSubsystem,
    monitor_holder: dict[str, ConnectionMonitor | None],
) -> LiveCtx:
    """Wire ConnectionMonitor, SyncEngine, WorkspaceManager, LRU eviction,
    and (if the resolver supports it) MediaPrefetcher, then assemble LiveCtx.
    """
    from backend.app.services.connection_monitor import ConnectionState
    from backend.app.services.proxy_resolver import LocalCacheOnlyResolver

    settings = core.settings
    cap_bytes = int(settings.media_cache_cap_gb) * 1024**3
    flags = arch.flags

    if flags.manual:
        initial_state = ConnectionState.disconnected
    elif flags.login_failed:
        initial_state = ConnectionState.offline
    else:
        initial_state = ConnectionState.online

    connection_monitor = ConnectionMonitor(
        provider=arch.archive,
        db_provider=lambda: core.db,
        interval_s=float(settings.health_probe_interval_s),
        timeout_s=float(settings.health_probe_timeout_s),
        event_bus=core.event_bus,
        forced_offline=flags.forced_offline,
        initial_state=initial_state,
        manual=flags.manual,
        logged_in=(lambda: arch.catdv.logged_in) if arch.catdv is not None else None,
    )
    # Publish the monitor so the archive subsystem's is_online closure can
    # read it (defer-read; the closure was installed before this point).
    monitor_holder["monitor"] = connection_monitor

    sync_engine = SyncEngine(
        provider=arch.archive,
        pending_ops_repo=core.pending_ops_repo,
        write_log_repo=core.write_log_repo,
        connection_monitor=connection_monitor,
        db_provider=lambda: core.db,
        event_bus=core.event_bus,
        tick_interval_s=float(settings.sync_tick_interval_s),
        retry_base_s=float(settings.sync_retry_base_s),
        retry_max_s=float(settings.sync_retry_max_s),
        max_attempts=int(settings.sync_max_attempts),
    )
    workspace_manager = WorkspaceManager(
        workspaces_repo=core.workspaces_repo,
        provider=arch.archive,
        proxy_resolver=arch.proxy_resolver,
        db_provider=lambda: core.db,
    )
    lru_eviction = LruEviction(
        actions=core.cache_actions,
        log_repo=core.cache_actions_log_repo,
        db_provider=lambda: core.db,
        media_cache_cap_bytes=cap_bytes,
        tick_interval_s=float(settings.lru_tick_interval_s),
    )
    media_cache_backend: MediaCacheBackend | None = None
    if arch.proxy_resolver is not None:
        media_cache_backend = build_media_cache_backend(
            media_cache=settings.media_cache,
            resolver=arch.proxy_resolver,
            ai_store=arch.ai_store,
            gcs=arch.gcs_service,
            proxy_cache_repo=core.proxy_cache_repo,
            db_provider=lambda: core.db,
        )

    media_prefetcher: MediaPrefetcher | None = None
    _inner_resolver = getattr(arch.proxy_resolver, "inner", arch.proxy_resolver)
    if (
        arch.proxy_resolver is not None
        and media_cache_backend is not None
        and not isinstance(_inner_resolver, LocalCacheOnlyResolver)
    ):
        media_prefetcher = MediaPrefetcher(
            queue_repo=core.prefetch_queue_repo,
            backend=media_cache_backend,
            db_provider=lambda: core.db,
            tick_interval_s=float(settings.prefetch_tick_interval_s),
        )

    idle_disconnector = None
    if flags.manual and arch.catdv is not None:
        from backend.app.services.idle_disconnector import IdleDisconnector

        idle_disconnector = IdleDisconnector(
            client=arch.catdv,
            monitor=connection_monitor,
            idle_timeout_s=float(settings.catdv_idle_logout_s),
        )

    return LiveCtx(
        core=core,
        archive=arch.archive,
        ai_store=arch.ai_store,
        gemini=arch.gemini,
        sync_engine=sync_engine,
        connection_monitor=connection_monitor,
        workspace_manager=workspace_manager,
        lru_eviction=lru_eviction,
        _gcs_service=arch.gcs_service,
        catdv=arch.catdv,
        proxy_resolver=arch.proxy_resolver,
        thumbnail_service=arch.thumbnail_service,
        media_cache_backend=media_cache_backend,
        media_prefetcher=media_prefetcher,
        idle_disconnector=idle_disconnector,
    )
