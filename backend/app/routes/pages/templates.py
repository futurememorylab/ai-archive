"""Shared Jinja2Templates instance for the page routers.

Registers the `smpte` global and the `bytes_human` / `comma` filters so
all route modules reach the same configured `templates` without duplicating
setup.
"""

import json as _json
from pathlib import Path

from fastapi.templating import Jinja2Templates

from backend.app.enums.registry import ENUM_REGISTRY
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

    Skipped for HTMX fragment renders (HX-Request) — those don't draw the topbar.
    Reads the counts the `_load_topbar_counts` page-router dependency computed
    (async, on the pooled connection) and stashed on `request.state` before this
    render — so the synchronous context processor itself runs zero I/O instead of
    opening a per-render sqlite connection (finding #10). Absent on requests that
    skipped the dependency → the chip falls back to its async /ui/sync-chip poll.
    Never raises (a context processor runs on EVERY render).
    """
    if request.headers.get("HX-Request") == "true":
        return {}
    counts = getattr(getattr(request, "state", None), "topbar_counts", None)
    if not counts:
        return {}
    state = getattr(getattr(request, "app", None), "state", None)
    monitor = getattr(getattr(state, "live_ctx", None), "connection_monitor", None)
    offline = monitor is not None and monitor.current_state().value != "online"
    return {
        "sync_counts": counts["sync_counts"],
        "offline": offline,
        "review_count": counts["review_count"],
    }


templates.context_processors.append(_topbar_sync_context)
