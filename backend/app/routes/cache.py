"""Cache management HTTP surface.

Two flavours of routes:

* JSON `/api/cache/...` — used by the inline badge popover and the bulk
  page; payloads are the inspector's frozen-dataclass shapes serialised
  via their `to_dict()` methods.
* HTML `/cache` and HTMX partials `/ui/cache-badge/...` /
  `/ui/cache-popover/...` — Jinja templates that match the styling of
  the PR 5 surfaces. Click-on-badge loads the popover inline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from backend.app.archive.model import ClipKey
from backend.app.deps import get_ctx
from backend.app.ui.view_models import cache_status_view

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


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


templates.env.filters["bytes_human"] = _bytes_human
templates.env.filters["comma"] = _comma

api_router = APIRouter(prefix="/api/cache", tags=["cache"])
page_router = APIRouter(tags=["cache"])
ui_router = APIRouter(prefix="/ui", tags=["cache"])


class EvictBody(BaseModel):
    layers: list[str] = []
    force: bool = False


class BulkEvictBody(BaseModel):
    clip_keys: list[tuple[str, str]] = []
    layers: list[str] = []
    force: bool = False


def _inspector(request: Request):
    ctx = get_ctx(request)
    if getattr(ctx, "cache_inspector", None) is None:
        raise HTTPException(503, "cache inspector not initialized")
    return ctx.cache_inspector


def _actions(request: Request):
    ctx = get_ctx(request)
    if getattr(ctx, "cache_actions", None) is None:
        raise HTTPException(503, "cache actions not initialized")
    return ctx.cache_actions


# --- JSON endpoints ------------------------------------------------


@api_router.get("/summary")
async def get_summary(request: Request) -> dict[str, Any]:
    insp = _inspector(request)
    return (await insp.summary()).to_dict()


@api_router.get("/orphans")
async def get_orphans(request: Request, deep: bool = False) -> list[dict]:
    insp = _inspector(request)
    statuses = await insp.list_orphans(deep=deep)
    return [s.to_dict() for s in statuses]


@api_router.get("/clip/{provider_id}/{clip_id}")
async def get_clip_status(request: Request, provider_id: str, clip_id: str) -> dict[str, Any]:
    insp = _inspector(request)
    key: ClipKey = (provider_id, clip_id)
    return (await insp.status_for_clip(key)).to_dict()


@api_router.post("/clip/{provider_id}/{clip_id}/evict")
async def evict_clip_layers(
    request: Request, provider_id: str, clip_id: str, body: EvictBody
) -> dict[str, Any]:
    actions = _actions(request)
    insp = _inspector(request)
    key: ClipKey = (provider_id, clip_id)
    if not body.layers:
        result = await actions.evict_clip_everywhere(key, force=body.force)
    else:
        result = await actions.bulk_evict([key], body.layers, force=body.force)
    status = await insp.status_for_clip(key)
    return {"status": status.to_dict(), "result": result.to_dict()}


@api_router.post("/bulk-evict")
async def bulk_evict(request: Request, body: BulkEvictBody) -> dict[str, Any]:
    actions = _actions(request)
    keys = [(p, c) for p, c in body.clip_keys]
    result = await actions.bulk_evict(keys, body.layers, force=body.force)
    return result.to_dict()


@api_router.post("/orphans/evict")
async def evict_orphans(request: Request) -> dict[str, Any]:
    actions = _actions(request)
    return (await actions.evict_orphans()).to_dict()


class PrefetchBody(BaseModel):
    clip_keys: list[tuple[str, str]] = []


@api_router.post("/prefetch")
async def prefetch_enqueue(request: Request, body: PrefetchBody) -> dict[str, Any]:
    ctx = get_ctx(request)
    ids: list[int] = []
    for prov, clip_id in body.clip_keys:
        rid = await ctx.prefetch_queue_repo.enqueue(
            ctx.db,
            key=(prov, clip_id),
            who="request",
        )
        ids.append(rid)
    return {"enqueued": len(body.clip_keys), "ids": ids}


@api_router.get("/prefetch/queue")
async def prefetch_queue_list(request: Request) -> dict[str, Any]:
    ctx = get_ctx(request)
    active = await ctx.prefetch_queue_repo.list_active(ctx.db)
    recent = await ctx.prefetch_queue_repo.list_recent(ctx.db, limit=50)
    counts = await ctx.prefetch_queue_repo.count_by_status(ctx.db)
    return {"active": active, "recent": recent, "counts": counts}


@api_router.post("/prefetch/{rid}/cancel")
async def prefetch_cancel(request: Request, rid: int) -> dict[str, Any]:
    ctx = get_ctx(request)
    ok = await ctx.prefetch_queue_repo.mark_cancelled(ctx.db, rid)
    if not ok:
        raise HTTPException(
            409,
            "row is not cancellable (downloading or already terminal)",
        )
    return {"cancelled": True}


# --- HTML pages + HTMX partials ------------------------------------


_VALID_TABS = {"all", "queue", "local", "ai"}


@page_router.get("/cache", response_class=HTMLResponse)
async def cache_page(
    request: Request,
    tab: str | None = None,
    store: str | None = None,
    workspace: int | None = None,
    orphans: int | None = None,
    evictable: int | None = None,
) -> HTMLResponse:
    insp = _inspector(request)
    ctx = get_ctx(request)

    tab_val = tab if tab in _VALID_TABS else "all"
    is_htmx = request.headers.get("HX-Request") == "true"

    # Always load queue rows — both the queue tab and the metric strip
    # use them, and the queries are cheap (status indexed).
    queue_active = await ctx.prefetch_queue_repo.list_active(ctx.db)
    queue_recent = await ctx.prefetch_queue_repo.list_recent(ctx.db, limit=50)
    queue_counts = await ctx.prefetch_queue_repo.count_by_status(ctx.db)

    if tab_val == "queue":
        # Queue tab doesn't need the inventory pass.
        rows_for_template: list = []
    else:
        if orphans:
            statuses = await insp.list_orphans()
        else:
            keys = await _all_cached_keys(ctx.db)
            statuses = await insp.status_for_clips(keys)
        rows = []
        for status in statuses:
            if store:
                ai_layer = status.layers[2]
                if not ai_layer.present or store not in (ai_layer.location or ""):
                    continue
            if workspace is not None:
                md_layer = status.layers[0]
                if workspace not in md_layer.pinned_by_workspaces:
                    continue
            if evictable:
                if not any(layer.evictable for layer in status.layers):
                    continue
            if tab_val == "local" and not status.layers[1].present:
                continue
            if tab_val == "ai" and not status.layers[2].present:
                continue
            rows.append(status)
        rows_for_template = [_cache_row(s) for s in rows]

    summary = await insp.summary()

    # Orphan totals for the metric strip. Computed once per request from
    # list_orphans() since CacheSummary does not surface these yet.
    orphan_statuses = await insp.list_orphans()
    orphan_count = len(orphan_statuses)
    orphan_bytes = sum(
        sum((layer.size_bytes or 0) for layer in s.layers if layer.evictable)
        for s in orphan_statuses
    )

    # Per-tab counts for the tab badges (always shown for all four).
    all_keys = await _all_cached_keys(ctx.db)
    all_statuses = await insp.status_for_clips(all_keys)
    counts = {
        "all": len(all_statuses),
        "local": sum(1 for s in all_statuses if s.layers[1].present),
        "ai": sum(1 for s in all_statuses if s.layers[2].present),
        "queue": queue_counts.get("queued", 0) + queue_counts.get("downloading", 0),
    }

    ai_total_count = sum(summary.counts_by_store.values())

    ctx_dict = {
        "summary": summary,
        "tab": tab_val,
        "rows": rows_for_template,
        "filters": {
            "store": store,
            "workspace": workspace,
            "orphans": bool(orphans),
            "evictable": bool(evictable),
        },
        "queue_active": queue_active,
        "queue_recent": queue_recent,
        "queue_counts": queue_counts,
        "orphan_count": orphan_count,
        "orphan_bytes": orphan_bytes,
        "ai_total_count": ai_total_count,
        "counts": counts,
    }

    if is_htmx:
        partial = (
            "pages/_cache_queue_table.html"
            if tab_val == "queue"
            else "pages/_cache_inventory_table.html"
        )
        return templates.TemplateResponse(request, partial, ctx_dict)
    return templates.TemplateResponse(request, "cache_page.html", ctx_dict)


@ui_router.get("/cache-badge/{provider_id}/{clip_id}", response_class=HTMLResponse)
async def cache_badge(request: Request, provider_id: str, clip_id: str) -> HTMLResponse:
    insp = _inspector(request)
    status = await insp.status_for_clip((provider_id, clip_id))
    return templates.TemplateResponse(
        request,
        "cache_badge.html",
        {"status": _status_for_template(status)},
    )


@ui_router.get("/cache-popover/{provider_id}/{clip_id}", response_class=HTMLResponse)
async def cache_popover(request: Request, provider_id: str, clip_id: str) -> HTMLResponse:
    insp = _inspector(request)
    status = await insp.status_for_clip((provider_id, clip_id))
    ctx = get_ctx(request)
    host_local_proxies = getattr(getattr(ctx, "proxy_resolver", None), "is_host_local", False)
    return templates.TemplateResponse(
        request,
        "cache_popover.html",
        {
            "status": _status_for_template(status),
            "host_local_proxies": host_local_proxies,
        },
    )


@ui_router.get("/cache/queue", response_class=HTMLResponse)
async def cache_queue_panel(request: Request) -> HTMLResponse:
    ctx = get_ctx(request)
    queue_active = await ctx.prefetch_queue_repo.list_active(ctx.db)
    queue_recent = await ctx.prefetch_queue_repo.list_recent(ctx.db, limit=50)
    queue_counts = await ctx.prefetch_queue_repo.count_by_status(ctx.db)
    return templates.TemplateResponse(
        request,
        "pages/_cache_queue_table.html",
        {
            "queue_active": queue_active,
            "queue_recent": queue_recent,
            "queue_counts": queue_counts,
        },
    )


# --- helpers ------------------------------------------------------


async def _all_cached_keys(db) -> list[ClipKey]:
    """Union of (provider_id, provider_clip_id) across the three layers."""
    keys: set[ClipKey] = set()
    cur = await db.execute("SELECT provider_id, provider_clip_id FROM clip_cache")
    for r in await cur.fetchall():
        keys.add((r[0], r[1]))
    cur = await db.execute("SELECT provider_id, provider_clip_id FROM proxy_cache")
    for r in await cur.fetchall():
        keys.add((r[0], r[1]))
    cur = await db.execute("SELECT DISTINCT provider_id, provider_clip_id FROM ai_store_files")
    for r in await cur.fetchall():
        keys.add((r[0], r[1]))
    return sorted(keys)


def _cache_row(status) -> dict:
    """Build a cache-inventory row in the shared _video_list shape, plus the
    cache-specific columns the cache row_cells partial reads."""
    pid, cid = status.clip_key
    md, local, ai = status.layers
    is_orphan = not md.present
    local_bytes = int(local.size_bytes or 0)
    ai_bytes = int(ai.size_bytes or 0)
    return {
        "select_value": f"{pid}/{cid}",
        "cache": cache_status_view(status),
        "thumb_url": f"/api/media/{cid}/thumb",
        "name": status.name,
        "name_sub": f"{pid}/{cid}",
        "row_href": None,
        "row_class": "orphan" if is_orphan else None,
        "row_bytes": local_bytes + ai_bytes,
        "clip_pid": pid,
        "clip_cid": cid,
        "workspace": ", ".join(str(w) for w in md.pinned_by_workspaces)
        if md.pinned_by_workspaces
        else "—",
        "local_bytes": local_bytes,
        "ai_bytes": ai_bytes,
    }


def _status_for_template(status) -> dict[str, Any]:
    """Templates expect dict-like access (e.g. `status.layers[0].present`).

    Jinja accepts attribute access on dicts but not tuples-of-dataclasses
    inside dataclasses cleanly. Convert to a plain dict tree via the
    inspector's `to_dict()`, then wrap layer dicts in a small dot-access
    shim so the templates can write `layer.present`.
    """
    d = status.to_dict()
    d["layers"] = [_DictWrap(layer) for layer in d["layers"]]
    return _DictWrap(d)


class _DictWrap:
    """Trivial attr-access over a dict for Jinja templates."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getattr__(self, name: str) -> Any:
        try:
            return self._data[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __getitem__(self, key) -> Any:
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def keys(self):
        return self._data.keys()
