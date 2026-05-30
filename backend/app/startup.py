"""Startup helpers — stale-session cleanup and external-dependency
liveness checks. Called from the FastAPI lifespan before serving."""

import logging
from dataclasses import dataclass, field

import aiosqlite

from backend.app.repositories.live_sessions import LiveSessionsRepo


async def run_startup_cleanup(conn: aiosqlite.Connection) -> int:
    """Drop stale-pending live_sessions older than 1h. Returns rows deleted."""
    repo = LiveSessionsRepo()
    return await repo.cleanup_stale_pending(conn, older_than_hours=1)


@dataclass
class StartupCheckResult:
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


async def run_checks(
    *,
    catdv,
    ai_store,
    proxy_resolver,
    catalog_id: int,
    sample_clip_id: int | None = None,
    verify_proxy: bool = False,
) -> StartupCheckResult:
    """Verify that external dependencies are reachable. Returns failures, never raises."""
    result = StartupCheckResult()

    try:
        if sample_clip_id is not None:
            await catdv.get_clip(sample_clip_id)
    except Exception as exc:  # noqa: BLE001
        result.failures.append(f"CatDV unreachable or sample clip missing: {exc}")

    try:
        health = await ai_store.health()
        if not health.ok:
            detail = health.detail or "unknown reason"
            result.failures.append(f"AI input store not healthy: {detail}")
    except Exception as exc:  # noqa: BLE001
        result.failures.append(f"AI input store check failed: {exc}")

    if verify_proxy and sample_clip_id is not None:
        try:
            await proxy_resolver.path_for_clip_id(sample_clip_id)
        except Exception as exc:  # noqa: BLE001
            result.failures.append(f"Proxy resolver failed for clip {sample_clip_id}: {exc}")

    return result


def warn_browser_secret_exposure(settings) -> None:
    """Log a WARNING when GEMINI_API_KEY is configured.

    live_sessions.mint_ephemeral_token returns the raw key to the
    browser because the ephemeral-token flow (authTokens.create) closes
    the WSS handshake with code 1007 'API key not valid' the moment the
    client sends `setup`. The accepted threat model is single-operator
    local app over VPN — see ADR 0043. This log line ensures the
    exposure is visible to the operator on every boot, not just to
    someone reading the code comment in live_sessions.py.
    """
    if getattr(settings, "gemini_api_key", None):
        logging.getLogger(__name__).warning(
            "GEMINI_API_KEY is configured; the raw key will be exposed to the "
            "browser during Live sessions. This is accepted under the "
            "single-operator local + VPN threat model — see ADR 0043. If your "
            "deployment falls outside that model, unset GEMINI_API_KEY."
        )
