"""Shared Jinja2Templates instance for the page routers.

Registers the `smpte` global and the `bytes_human` / `comma` filters so
all route modules reach the same configured `templates` without duplicating
setup.
"""

import json as _json
import sqlite3
from pathlib import Path

from fastapi.templating import Jinja2Templates

from backend.app.enums.registry import ENUM_REGISTRY
from backend.app.repositories.review_items import FOR_REVIEW_WHERE
from backend.app.services.word_diff import diff_html
from backend.app.timecode import secs_to_smpte

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["smpte"] = secs_to_smpte
templates.env.globals["diff_html"] = diff_html
# Media-cache backend ("local" dev proxy cache vs "ai_store" cloud/GCS).
# Templates branch on this to hide the unused local-media layer in cloud.
# Seeded to the safe default here; the app lifespan overrides it from
# settings.media_cache at startup so HTMX fragment renders see the real value.
templates.env.globals["media_cache"] = "local"


def _bytes_human(n: int | None) -> str:
    if not n:
        return "0 B"
    n = int(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _comma(n: int | None) -> str:
    if n is None:
        return "0"
    return f"{int(n):,}"


def _usd(x: float | None) -> str:
    """Server-side mirror of static/format.js `fmtUsd`: None → em dash;
    under $0.10 → 3 decimals (small per-clip costs need the precision);
    otherwise 2 decimals; always a '$' prefix."""
    if x is None:
        return "—"
    try:
        n = float(x)
    except (TypeError, ValueError):
        return "—"
    return f"${n:.3f}" if n < 0.1 else f"${n:.2f}"


templates.env.filters["bytes_human"] = _bytes_human
templates.env.filters["comma"] = _comma
templates.env.filters["usd"] = _usd


def _fixed_enums_json() -> str:
    """Static (DB-free) fixed-enum values for window.APP_ENUMS. Editable enums
    are intentionally excluded — they change at runtime and are delivered via
    route context / the JSON API."""
    data = {
        key: [v.value for v in spec.values]
        for key, spec in ENUM_REGISTRY.items()
        if not spec.editable
    }
    return _json.dumps(data)


templates.env.globals["app_enums_json"] = _fixed_enums_json()


def _topbar_sync_context(request) -> dict[str, object]:
    """Render the topbar sync chip's counts INLINE on full-page loads, so the
    chip shows the real "↑ N" / "✓ Synced" on first paint instead of flickering
    through a placeholder while an async load-fetch returns.

    Skipped for HTMX fragment renders (HX-Request) — those don't draw the topbar,
    and it keeps the read off the hot fragment-poll paths. Reads pending_operations
    directly with a short synchronous SQLite query rather than the async
    PendingOperationsRepo, because a Jinja context processor is synchronous and
    can't await; WAL mode serves concurrent readers without blocking the app's
    own connection. The query mirrors count_actionable exactly so the inline
    value and the /ui/sync-chip poll always agree.
    """
    if request.headers.get("HX-Request") == "true":
        return {}
    state = getattr(getattr(request, "app", None), "state", None)
    core = getattr(state, "core_ctx", None)
    data_dir = getattr(getattr(core, "settings", None), "data_dir", None)
    if data_dir is None:
        return {}
    # A context processor runs on EVERY full-page render, so it must never raise
    # (a failure would 500 every page). Any problem → return {} and let the chip
    # fall back to its load-fetch. Broad except is intentional here.
    try:
        conn = sqlite3.connect(str(data_dir / "app.db"), timeout=0.5)
        try:
            rows = dict(
                conn.execute(
                    "SELECT status, COUNT(*) FROM pending_operations "
                    "WHERE status IN ('pending','in_flight','failed','conflict') "
                    "GROUP BY status"
                ).fetchall()
            )
            # "To review" count for the topbar; the shared FOR_REVIEW_WHERE
            # predicate keeps this full-render (sync) path identical to the
            # /?anno=for_review filter and the /ui/review-pill async count.
            review_row = conn.execute(
                f"SELECT COUNT(DISTINCT catdv_clip_id) FROM review_items WHERE {FOR_REVIEW_WHERE}"
            ).fetchone()
        finally:
            conn.close()
        counts = {
            "queued": rows.get("pending", 0) + rows.get("in_flight", 0),
            "problems": rows.get("failed", 0) + rows.get("conflict", 0),
        }
        review_count = review_row[0] if review_row else 0
        monitor = getattr(getattr(state, "live_ctx", None), "connection_monitor", None)
        offline = monitor is not None and monitor.current_state().value != "online"
    except Exception:  # noqa: BLE001 - must never break page rendering
        return {}
    return {"sync_counts": counts, "offline": offline, "review_count": review_count}


templates.context_processors.append(_topbar_sync_context)
