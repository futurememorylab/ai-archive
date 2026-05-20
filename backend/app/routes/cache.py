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

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

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
    ctx = request.app.state.ctx
    if getattr(ctx, "cache_inspector", None) is None:
        raise HTTPException(503, "cache inspector not initialized")
    return ctx.cache_inspector


def _actions(request: Request):
    ctx = request.app.state.ctx
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
async def get_clip_status(
    request: Request, provider_id: str, clip_id: str
) -> dict[str, Any]:
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
async def bulk_evict(
    request: Request, body: BulkEvictBody
) -> dict[str, Any]:
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
async def prefetch_enqueue(
    request: Request, body: PrefetchBody
) -> dict[str, Any]:
    ctx = request.app.state.ctx
    ids: list[int] = []
    for prov, clip_id in body.clip_keys:
        rid = await ctx.prefetch_queue_repo.enqueue(
            ctx.db, key=(prov, clip_id), who="request",
        )
        ids.append(rid)
    return {"enqueued": len(body.clip_keys), "ids": ids}


@api_router.get("/prefetch/queue")
async def prefetch_queue_list(request: Request) -> dict[str, Any]:
    ctx = request.app.state.ctx
    active = await ctx.prefetch_queue_repo.list_active(ctx.db)
    recent = await ctx.prefetch_queue_repo.list_recent(ctx.db, limit=50)
    counts = await ctx.prefetch_queue_repo.count_by_status(ctx.db)
    return {"active": active, "recent": recent, "counts": counts}


@api_router.post("/prefetch/{rid}/cancel")
async def prefetch_cancel(
    request: Request, rid: int
) -> dict[str, Any]:
    ctx = request.app.state.ctx
    ok = await ctx.prefetch_queue_repo.mark_cancelled(ctx.db, rid)
    if not ok:
        raise HTTPException(
            409,
            "row is not cancellable (downloading or already terminal)",
        )
    return {"cancelled": True}


# --- HTML pages + HTMX partials ------------------------------------


@page_router.get("/cache", response_class=HTMLResponse)
async def cache_page(
    request: Request,
    store: str | None = None,
    workspace: int | None = None,
    orphans: int | None = None,
    evictable: int | None = None,
) -> HTMLResponse:
    insp = _inspector(request)
    summary = await insp.summary()
    if orphans:
        statuses = await insp.list_orphans()
    else:
        # Default page: list every clip with at least one cache layer.
        # We pull from clip_cache + proxy_cache + ai_store_files keys.
        ctx = request.app.state.ctx
        keys = await _all_cached_keys(ctx.db)
        statuses = await insp.status_for_clips(keys)
    # Filter pass
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
        rows.append(status)
    return templates.TemplateResponse(
        request,
        "cache_page.html",
        {
            "summary": summary,
            "rows": [_status_for_template(s) for s in rows],
            "filters": {
                "store": store,
                "workspace": workspace,
                "orphans": bool(orphans),
                "evictable": bool(evictable),
            },
        },
    )


@ui_router.get("/cache-badge/{provider_id}/{clip_id}",
               response_class=HTMLResponse)
async def cache_badge(
    request: Request, provider_id: str, clip_id: str
) -> HTMLResponse:
    insp = _inspector(request)
    status = await insp.status_for_clip((provider_id, clip_id))
    return templates.TemplateResponse(
        request,
        "cache_badge.html",
        {"status": _status_for_template(status)},
    )


@ui_router.get("/cache-popover/{provider_id}/{clip_id}",
               response_class=HTMLResponse)
async def cache_popover(
    request: Request, provider_id: str, clip_id: str
) -> HTMLResponse:
    insp = _inspector(request)
    status = await insp.status_for_clip((provider_id, clip_id))
    return templates.TemplateResponse(
        request,
        "cache_popover.html",
        {"status": _status_for_template(status)},
    )


# --- helpers ------------------------------------------------------


async def _all_cached_keys(db) -> list[ClipKey]:
    """Union of (provider_id, provider_clip_id) across the three layers."""
    keys: set[ClipKey] = set()
    cur = await db.execute(
        "SELECT provider_id, provider_clip_id FROM clip_cache"
    )
    for r in await cur.fetchall():
        keys.add((r[0], r[1]))
    cur = await db.execute(
        "SELECT provider_id, provider_clip_id FROM proxy_cache"
    )
    for r in await cur.fetchall():
        keys.add((r[0], r[1]))
    cur = await db.execute(
        "SELECT DISTINCT provider_id, provider_clip_id FROM ai_store_files"
    )
    for r in await cur.fetchall():
        keys.add((r[0], r[1]))
    return sorted(keys)


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
