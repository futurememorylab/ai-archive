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


def log_live_token_mode(settings) -> None:
    """Log how Live audio authenticates, at boot.

    The raw GEMINI_API_KEY is no longer sent to the browser:
    live_sessions.mint_ephemeral_token now mints a short-lived, config-bound
    ephemeral token server-side, which the browser presents as `?access_token=`
    (see ADR 0111, supersedes 0043). When the key is present we log the secure
    posture at INFO; when it is missing we warn that Live audio is unavailable.
    """
    log = logging.getLogger(__name__)
    if getattr(settings, "gemini_api_key", None):
        log.info(
            "Live audio authenticates with short-lived, config-bound ephemeral "
            "tokens (auth_tokens.create); the GEMINI_API_KEY stays server-side and "
            "is never sent to the browser (ADR 0111)."
        )
    else:
        log.warning(
            "GEMINI_API_KEY is not configured; Live audio will be unavailable "
            "(cannot mint ephemeral tokens)."
        )
