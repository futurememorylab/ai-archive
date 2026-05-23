"""Workspaces HTTP surface.

CRUD + prep (SSE) + release. The `prepare` endpoint streams one event
per state transition per clip; the route is plain JSON-over-SSE rather
than HTMX so the workspace switcher can subscribe directly.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


class WorkspaceCreate(BaseModel):
    name: str
    provider_id: str = "catdv"
    catalog_id: str
    description: str | None = None
    clip_keys: list[tuple[str, str]] = []


class ClipKeysBody(BaseModel):
    clip_keys: list[tuple[str, str]]


def _require_manager(request: Request):
    ctx = request.app.state.ctx
    if getattr(ctx, "workspace_manager", None) is None:
        raise HTTPException(503, "workspace manager not initialized")
    return ctx


@router.get("")
async def list_workspaces(request: Request) -> list[dict[str, Any]]:
    ctx = _require_manager(request)
    return await ctx.workspace_manager.list_workspaces()


@router.post("", status_code=201)
async def create_workspace(request: Request, body: WorkspaceCreate) -> dict:
    ctx = _require_manager(request)
    ws_id = await ctx.workspace_manager.create_workspace(
        name=body.name,
        provider_id=body.provider_id,
        catalog_id=body.catalog_id,
        description=body.description,
        clip_keys=[(p, c) for p, c in body.clip_keys],
    )
    return {"id": ws_id}


@router.get("/{ws_id}")
async def get_workspace(request: Request, ws_id: int) -> dict:
    ctx = _require_manager(request)
    ws = await ctx.workspace_manager.get(ws_id)
    if ws is None:
        raise HTTPException(404, "workspace not found")
    return ws


@router.post("/{ws_id}/clips")
async def add_clips(request: Request, ws_id: int, body: ClipKeysBody) -> dict:
    ctx = _require_manager(request)
    await ctx.workspace_manager.add_clips(ws_id, [(p, c) for p, c in body.clip_keys])
    return {"id": ws_id, "added": len(body.clip_keys)}


@router.delete("/{ws_id}/clips/{provider_id}/{clip_id}")
async def remove_clip(request: Request, ws_id: int, provider_id: str, clip_id: str) -> dict:
    ctx = _require_manager(request)
    await ctx.workspace_manager.remove_clips(ws_id, [(provider_id, clip_id)])
    return {"id": ws_id, "removed": 1}


@router.post("/{ws_id}/prepare")
async def prepare_workspace(request: Request, ws_id: int) -> StreamingResponse:
    """Stream prep progress as SSE."""
    ctx = _require_manager(request)

    async def gen():
        try:
            async for ev in ctx.workspace_manager.prepare(ws_id):
                payload = {
                    "clip_key": list(ev.clip_key),
                    "state": ev.state,
                    "error": ev.error,
                }
                yield f"data: {json.dumps(payload)}\n\n"
        except LookupError as exc:
            yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/{ws_id}/release")
async def release_workspace(request: Request, ws_id: int, delete: bool = False) -> dict:
    ctx = _require_manager(request)
    await ctx.workspace_manager.release(ws_id, delete_workspace=delete)
    return {"id": ws_id, "released": True, "deleted": delete}


# Unused import-guard
_ = asdict
