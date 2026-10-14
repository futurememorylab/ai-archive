"""Active catalog (#124): which catalog this browser is working in, and the
cached list of catalogs it can switch to.

The active catalog is per browser (cookie), defaulting to
`settings.catdv_catalog_id`. The list is a small cache layer: DB rows
(`catalogs` table) refreshed from the archive provider only when online and
older than CATALOG_LIST_TTL; any miss / failure degrades to the stale rows.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from backend.app.archive.model import CatalogInfo
from backend.app.services.connection_monitor import ConnectionState
from backend.app.services.errors import humanise

if TYPE_CHECKING:
    from starlette.requests import Request

logger = logging.getLogger(__name__)

COOKIE = "active_catalog"
CATALOG_LIST_TTL = timedelta(hours=1)

# ASCII digits only, bounded: str.isdigit() accepts "²" (→ int() ValueError
# in the CatDV adapter) and unbounded strings would be sent to CatDV verbatim.
_CATALOG_ID_RE = re.compile(r"[0-9]{1,18}")


def is_catalog_id(raw: str) -> bool:
    return _CATALOG_ID_RE.fullmatch(raw) is not None


def active_catalog_id(request: Request, settings: Any) -> str:
    raw = request.cookies.get(COOKIE, "")
    return raw if is_catalog_id(raw) else str(settings.catdv_catalog_id)


def is_live_online(live: Any) -> bool:
    if live is None:
        return False
    monitor = live.connection_monitor
    if getattr(monitor, "is_forced", False) or getattr(monitor, "_forced_offline", False):
        return False
    return monitor.current_state() == ConnectionState.online


def _fallback(catalog_id: str) -> CatalogInfo:
    return CatalogInfo(catalog_id, f"Catalog #{catalog_id}", False)


async def list_catalogs(
    core: Any, live: Any, active_id: str, *, now: datetime | None = None
) -> list[CatalogInfo]:
    now = now or datetime.now(UTC)
    provider_id = core.settings.archive_provider
    repo = core.catalogs_repo
    rows = await repo.list(core.db, provider_id=provider_id)

    if is_live_online(live):
        stamp = await repo.fetched_at(core.db, provider_id=provider_id)
        stale = not rows or stamp is None or datetime.fromisoformat(stamp) < now - CATALOG_LIST_TTL
        if stale:
            try:
                fresh = await live.archive.list_catalogs()
                if not fresh:
                    raise ValueError("archive returned no catalogs")
                await repo.replace_all(
                    core.db, provider_id=provider_id, catalogs=fresh, fetched_at=now.isoformat()
                )
                rows = await repo.list(core.db, provider_id=provider_id)
            except Exception as exc:  # stale list beats no picker
                logger.warning("catalog list refresh failed: %s", humanise(exc))
                if rows:
                    # Re-stamp the stale rows so a failing CatDV is retried once
                    # per TTL, not on every topbar load in every tab.
                    await repo.replace_all(
                        core.db, provider_id=provider_id, catalogs=rows, fetched_at=now.isoformat()
                    )

    if all(c.id != active_id for c in rows):
        rows.append(_fallback(active_id))
    return rows


async def catalog_name(core: Any, catalog_id: str) -> str:
    rows = await core.catalogs_repo.list(core.db, provider_id=core.settings.archive_provider)
    return next((c.name for c in rows if c.id == catalog_id), _fallback(catalog_id).name)
