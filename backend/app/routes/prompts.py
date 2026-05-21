"""REST API for prompt management.

Verb-style sub-paths (`:archive`, `:promote`, `:duplicate`, `:restore`) keep
state mutations visually distinct from RESTful CRUD; FastAPI maps them as
literal path strings.
"""
from typing import Any

import aiosqlite
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.app.models.prompt import Prompt, PromptVersion, TargetMap
from backend.app.repositories.prompts import VersionImmutableError

router = APIRouter(prefix="/api/prompts", tags=["prompts"])


# ── request models ──────────────────────────────────────────────────────────


class PromptCreate(BaseModel):
    name: str
    description: str | None = None
    body: str
    target_map: TargetMap
    output_schema: dict
    model: str


class PromptPatch(BaseModel):
    name: str | None = None
    description: str | None = None


class PromptDuplicate(BaseModel):
    name: str | None = None
    description: str | None = None


class VersionCreate(BaseModel):
    from_version_id: int | None = None


class VersionEdit(BaseModel):
    body: str
    target_map: TargetMap
    output_schema: dict
    model: str


# ── response shaping ────────────────────────────────────────────────────────


def _prompt_envelope(prompt: Prompt, versions: list[PromptVersion]) -> dict[str, Any]:
    """Render full detail: prompt + all versions + convenience pointers."""
    prod = next((v for v in versions if v.state == "production"), None)
    latest = versions[0].id if versions else None  # versions are desc by version_num
    return {
        **prompt.model_dump(),
        "current_production_version_id": prod.id if prod else None,
        "current_production_version_num": prod.version_num if prod else None,
        "latest_version_id": latest,
        "versions": [_version_envelope(v) for v in versions],
    }


def _version_envelope(v: PromptVersion) -> dict[str, Any]:
    out = v.model_dump()
    # TargetMap is a RootModel; exclude_unset so optional TargetEntry fields
    # (identifier, target, mode) are omitted when not explicitly set, keeping
    # the wire shape compact and matching what was originally sent.
    out["target_map"] = (
        v.target_map.model_dump(exclude_unset=True)
        if hasattr(v.target_map, "model_dump")
        else v.target_map
    )
    return out


# ── prompt-level routes ─────────────────────────────────────────────────────


@router.get("")
async def list_prompts(request: Request, archived: int = 0):
    ctx = request.app.state.ctx
    if archived:
        rows = await ctx.prompts_repo.list_archived(ctx.db)
    else:
        rows = await ctx.prompts_repo.list_active(ctx.db)
    results: list[dict[str, Any]] = []
    for p in rows:
        _, versions = await ctx.prompts_repo.get_with_versions(ctx.db, p.id)
        prod = next((v for v in versions if v.state == "production"), None)
        results.append({
            **p.model_dump(),
            "current_production_version_id": prod.id if prod else None,
            "current_production_version_num": prod.version_num if prod else None,
        })
    return results


@router.get("/{prompt_id}")
async def get_prompt(request: Request, prompt_id: int):
    ctx = request.app.state.ctx
    try:
        prompt, versions = await ctx.prompts_repo.get_with_versions(ctx.db, prompt_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    return _prompt_envelope(prompt, versions)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_prompt(request: Request, body: PromptCreate):
    ctx = request.app.state.ctx
    try:
        pid, _ = await ctx.prompts_repo.create_with_initial_version(
            ctx.db,
            name=body.name,
            description=body.description,
            body=body.body,
            target_map=body.target_map,
            output_schema=body.output_schema,
            model=body.model,
        )
    except aiosqlite.IntegrityError as exc:
        raise HTTPException(409, f"name collision: {exc}")
    return {"id": pid}


@router.patch("/{prompt_id}")
async def patch_prompt(request: Request, prompt_id: int, body: PromptPatch):
    ctx = request.app.state.ctx
    try:
        await ctx.prompts_repo.update_metadata(
            ctx.db, prompt_id, name=body.name, description=body.description
        )
    except aiosqlite.IntegrityError as exc:
        raise HTTPException(409, f"name collision: {exc}")
    return {"id": prompt_id}


@router.post("/{prompt_id}:archive")
async def archive_prompt(request: Request, prompt_id: int):
    ctx = request.app.state.ctx
    await ctx.prompts_repo.archive(ctx.db, prompt_id)
    return {"id": prompt_id, "archived": True}


@router.post("/{prompt_id}:restore")
async def restore_prompt(request: Request, prompt_id: int):
    ctx = request.app.state.ctx
    await ctx.prompts_repo.restore(ctx.db, prompt_id)
    return {"id": prompt_id, "archived": False}


@router.post("/{prompt_id}:duplicate", status_code=status.HTTP_201_CREATED)
async def duplicate_prompt(
    request: Request, prompt_id: int, body: PromptDuplicate | None = None
):
    ctx = request.app.state.ctx
    name = (body.name.strip() if body and body.name else None) or None
    description = body.description if body else None
    try:
        new_pid, _ = await ctx.prompts_repo.duplicate(
            ctx.db, prompt_id, name=name, description=description
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except aiosqlite.IntegrityError:
        raise HTTPException(409, f"A prompt named {name!r} already exists.")
    return {"id": new_pid}


# ── version-level routes ────────────────────────────────────────────────────


@router.get("/{prompt_id}/versions/{version_id}")
async def get_version(request: Request, prompt_id: int, version_id: int):
    ctx = request.app.state.ctx
    try:
        v = await ctx.prompts_repo.get_version(ctx.db, version_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    if v.prompt_id != prompt_id:
        raise HTTPException(404, "version does not belong to prompt")
    return _version_envelope(v)


@router.post("/{prompt_id}/versions", status_code=status.HTTP_201_CREATED)
async def create_version(request: Request, prompt_id: int, body: VersionCreate):
    ctx = request.app.state.ctx
    try:
        new_vid = await ctx.prompts_repo.create_version(
            ctx.db, prompt_id, from_version_id=body.from_version_id
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    return {"id": new_vid}


@router.put("/{prompt_id}/versions/{version_id}")
async def update_version(
    request: Request, prompt_id: int, version_id: int, body: VersionEdit
):
    ctx = request.app.state.ctx
    try:
        v = await ctx.prompts_repo.get_version(ctx.db, version_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    if v.prompt_id != prompt_id:
        raise HTTPException(404, "version does not belong to prompt")
    try:
        await ctx.prompts_repo.update_version(
            ctx.db,
            version_id,
            body=body.body,
            target_map=body.target_map,
            output_schema=body.output_schema,
            model=body.model,
        )
    except VersionImmutableError as exc:
        return JSONResponse(
            {"error_code": "version_immutable", "message": str(exc)},
            status_code=409,
        )
    return {"id": version_id}


@router.post("/{prompt_id}/versions/{version_id}:promote")
async def promote_version(request: Request, prompt_id: int, version_id: int):
    ctx = request.app.state.ctx
    try:
        v = await ctx.prompts_repo.get_version(ctx.db, version_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    if v.prompt_id != prompt_id:
        raise HTTPException(404, "version does not belong to prompt")
    try:
        await ctx.prompts_repo.promote_version(ctx.db, prompt_id, version_id)
    except VersionImmutableError as exc:
        return JSONResponse(
            {"error_code": "version_immutable", "message": str(exc)},
            status_code=409,
        )
    return {"id": version_id, "state": "production"}


@router.get("/{prompt_id}/versions/{version_id}/export")
async def export_version(request: Request, prompt_id: int, version_id: int):
    ctx = request.app.state.ctx
    try:
        prompt, _ = await ctx.prompts_repo.get_with_versions(ctx.db, prompt_id)
        v = await ctx.prompts_repo.get_version(ctx.db, version_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    if v.prompt_id != prompt_id:
        raise HTTPException(404, "version does not belong to prompt")
    return {
        "prompt": {"name": prompt.name, "description": prompt.description},
        "version": {
            "version_num": v.version_num,
            "state": v.state,
            "body": v.body,
            "target_map": (
                v.target_map.model_dump(exclude_unset=True)
                if hasattr(v.target_map, "model_dump")
                else v.target_map
            ),
            "output_schema": v.output_schema,
            "model": v.model,
        },
    }
