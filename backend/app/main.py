"""FastAPI app factory and lifespan. Wires routers, mounts static assets,
and owns the context lifecycle (build CoreCtx + LiveCtx at startup, aclose
at shutdown to release the CatDV session seat)."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from backend.app.auth.errors import NotAuthenticated
from backend.app.auth.identity import resolve_user
from backend.app.auth.models import CurrentUser
from backend.app.context import build_context
from backend.app.logging_setup import configure_logging
from backend.app.routes.batches import router as batches_router
from backend.app.routes.cache import api_router as cache_api_router
from backend.app.routes.cache import page_router as cache_page_router
from backend.app.routes.cache import ui_router as cache_ui_router
from backend.app.routes.catdv import router as catdv_router
from backend.app.routes.connection import router as connection_router
from backend.app.routes.enums import router as enums_router
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
from backend.app.routes.vpn import router as vpn_router
from backend.app.routes.workspaces import router as workspaces_router
from backend.app.seed import seed_default_prompt, seed_live_system_instruction
from backend.app.services.connection_monitor import ConnectionState
from backend.app.settings import Settings
from backend.app.startup import log_live_token_mode, run_startup_cleanup

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
    log_live_token_mode(settings)
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


async def _refresh_topbar_counts(request: Request) -> None:
    """Refresh the cached topbar counts before a full-page render, so the
    synchronous Jinja context processor reads a current value from memory
    instead of opening its own per-render sqlite connection (finding #10).
    Skipped for HTMX fragments (they don't draw the topbar); never fatal."""
    if request.headers.get("hx-request") == "true":
        return
    core = getattr(request.app.state, "core_ctx", None)
    if core is None:
        return
    try:
        await core.refresh_topbar_counts()
    except Exception:  # noqa: BLE001 — a count refresh must never break a page
        logging.getLogger(__name__).debug("topbar count refresh failed", exc_info=True)


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
    app.include_router(vpn_router)
    app.include_router(cache_api_router)
    app.include_router(cache_page_router)
    app.include_router(cache_ui_router)
    app.include_router(enums_router)
    for r in page_routers:
        app.include_router(r, dependencies=[Depends(_refresh_topbar_counts)])
    app.include_router(live_router)


app = FastAPI(title="CatDV Annotator", lifespan=lifespan)
_STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


_timing_log = logging.getLogger("backend.app.timing")
_auth_log = logging.getLogger("backend.app.auth")


# Paths reachable WITHOUT an active role (everything else is default-deny under
# AUTH_BACKEND=iap). Keep this list tiny and explicit — forgetting a public
# path is harmless; the opt-out shape means we never forget a protected one.
_AUTH_ALLOWLIST = ("/static/", "/api/health", "/access", "/favicon.ico")


def _is_allowlisted(path: str) -> bool:
    return any(
        path == p or path.startswith(p.rstrip("/") + "/") or path == p.rstrip("/")
        for p in _AUTH_ALLOWLIST
    )


def _deny(request: Request, email: str | None) -> Response:
    """Fail-closed denial. JSON for HTMX/fetch callers; the access page (403)
    for a browser navigation."""
    wants_json = request.headers.get("hx-request") or "application/json" in request.headers.get(
        "accept", ""
    )
    if wants_json:
        return JSONResponse({"detail": "access not granted"}, status_code=403)
    from backend.app.routes.pages.templates import templates

    return templates.TemplateResponse(
        request,
        "pages/access.html",
        {"state": "denied", "email": email},
        status_code=403,
    )


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


@app.middleware("http")
async def _auth_gate(request: Request, call_next):
    """Resolve identity, attach it (with role) for the layout, and — under
    AUTH_BACKEND=iap — enforce default-deny on every non-allow-listed path.

    Fail-closed: any failure to establish a trustworthy identity, or any
    error in the role lookup, denies. Under AUTH_BACKEND=dev the single local
    operator is implicit admin and nothing is gated (local dev stays usable;
    no IAP path is exercised). See ADR 0084 + spec
    2026-06-14-iap-roles-admin-console-design.md.
    """
    request.state.current_user = None
    core = getattr(request.app.state, "core_ctx", None)
    path = request.url.path
    if core is None or path.startswith("/static/"):
        return await call_next(request)

    settings = core.settings

    # Resolve identity — cheap, no DB. Fail-closed → anonymous.
    try:
        ident = resolve_user(request, settings)
    except (NotAuthenticated, RuntimeError):
        ident = None

    # Dev: the single local operator is implicit admin; nothing is gated.
    if settings.auth_backend != "iap":
        if ident is not None:
            request.state.current_user = CurrentUser(email=ident.email, role="admin")
        return await call_next(request)

    # IAP: attach identity (role unknown yet) so /access can show who you are.
    email = ident.email if ident else None
    if email:
        request.state.current_user = CurrentUser(email=email, role=None)

    # Allow-list short-circuit BEFORE any DB work (health probes hit this often).
    if _is_allowlisted(path):
        return await call_next(request)

    # Authorize: one read returns (role, status); fail-closed on any error.
    gate = None
    if email:
        try:
            gate = await core.user_roles_repo.get_gate_state(core.db, email)
        except Exception:  # noqa: BLE001 — any lookup error denies, never admits
            gate = None
    if gate is None:
        return _deny(request, email)
    role, status = gate

    request.state.current_user = CurrentUser(email=email, role=role)
    # Steady-state browsing is READ-ONLY: the gate writes ONLY to flip an invited
    # user to active on first sight (issue #73 — the old per-request UPDATE+commit
    # was the Litestream lock contention that crashed the container). The flip is
    # best-effort: a transient write-lock must NEVER take the request down — an
    # invited user already admits and flips on a later request.
    # Regression guard: tests/integration/test_auth_gate.py (prod outage 2026-06-17).
    if status == "invited":
        try:
            await core.user_roles_repo.activate_on_first_sight(core.db, email)
        except Exception:  # noqa: BLE001 — bookkeeping write; never block the request
            _auth_log.warning("activate_on_first_sight failed (non-fatal); continuing", exc_info=True)
    return await call_next(request)


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
