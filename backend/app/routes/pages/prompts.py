"""Prompt-management HTML pages and form actions: list, detail, CRUD."""

import json

import aiosqlite
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import ValidationError

from backend.app.deps import get_core_ctx
from backend.app.models.prompt import TargetMap
from backend.app.repositories.prompts import VersionImmutableError
from backend.app.routes.pages.templates import templates

router = APIRouter(tags=["pages"])


@router.get("/prompts", response_class=HTMLResponse)
async def prompts_page(request: Request, archived: int = 0):
    ctx = get_core_ctx(request)
    repo = ctx.prompts_repo
    prompts = await (repo.list_archived(ctx.db) if archived else repo.list_active(ctx.db))
    selected = None
    selected_version = None
    versions: list = []
    if prompts:
        first_id = prompts[0].id
        selected, versions = await repo.get_with_versions(ctx.db, first_id)
        selected_version = _pick_default_version(versions)
    return templates.TemplateResponse(
        request,
        "pages/prompts.html",
        {
            "prompts": [p.model_dump() for p in prompts],
            "selected": selected.model_dump() if selected else None,
            "selected_version": _version_view(selected_version) if selected_version else None,
            "versions": [_version_view(v) for v in versions],
            "archived_view": bool(archived),
            "rail_active": "prompts",
        },
    )


@router.get("/prompts/archived", response_class=HTMLResponse)
async def prompts_archived_page(request: Request):
    return await prompts_page(request, archived=1)


@router.get("/prompts/new", response_class=HTMLResponse)
async def prompt_new_page(request: Request):
    ctx = get_core_ctx(request)
    prompts = await ctx.prompts_repo.list_active(ctx.db)
    return templates.TemplateResponse(
        request,
        "pages/_prompt_new.html",
        {
            "prompts": [p.model_dump() for p in prompts],
            "rail_active": "prompts",
            "error": None,
            "form": {
                "name": "",
                "description": "",
                "body": "",
                "target_map_text": "{}",
                "output_schema_text": "{}",
                "model": "gemini-2.5-flash-lite",
                "media_kind": "any",
            },
        },
    )


@router.post("/prompts/_create")
async def action_create_prompt(request: Request):
    ctx = get_core_ctx(request)
    form = await request.form()
    name = (form.get("name") or "").strip()
    description = (form.get("description") or "").strip() or None
    body = form.get("body") or ""
    target_map_text = form.get("target_map") or "{}"
    output_schema_text = form.get("output_schema") or "{}"
    model = form.get("model") or "gemini-2.5-flash-lite"
    media_kind = str(form.get("media_kind") or "any")
    error = None
    target_map = None
    output_schema = None
    try:
        target_map = json.loads(target_map_text)
        output_schema = json.loads(output_schema_text)
    except json.JSONDecodeError as exc:
        error = f"invalid JSON: {exc}"
    if error is None:
        try:
            TargetMap.model_validate(target_map)
        except ValidationError as exc:
            error = f"invalid target_map: {exc.errors()[0]['msg']}"
    if not name:
        error = "name is required"
    if error:
        prompts = await ctx.prompts_repo.list_active(ctx.db)
        return templates.TemplateResponse(
            request,
            "pages/_prompt_new.html",
            {
                "prompts": [p.model_dump() for p in prompts],
                "rail_active": "prompts",
                "error": error,
                "form": {
                    "name": name,
                    "description": description or "",
                    "body": body,
                    "target_map_text": target_map_text,
                    "output_schema_text": output_schema_text,
                    "model": model,
                    "media_kind": media_kind,
                },
            },
            status_code=400,
        )
    try:
        pid, _ = await ctx.prompts_repo.create_with_initial_version(
            ctx.db,
            name=name,
            description=description,
            body=body,
            target_map=target_map,
            output_schema=output_schema,
            model=model,
            media_kind=media_kind,
        )
    except aiosqlite.IntegrityError as exc:
        prompts = await ctx.prompts_repo.list_active(ctx.db)
        return templates.TemplateResponse(
            request,
            "pages/_prompt_new.html",
            {
                "prompts": [p.model_dump() for p in prompts],
                "rail_active": "prompts",
                "error": f"name already exists: {exc}",
                "form": {
                    "name": name,
                    "description": description or "",
                    "body": body,
                    "target_map_text": target_map_text,
                    "output_schema_text": output_schema_text,
                    "model": model,
                    "media_kind": media_kind,
                },
            },
            status_code=400,
        )
    return RedirectResponse(f"/prompts/{pid}", status_code=303)


@router.get("/prompts/{prompt_id}", response_class=HTMLResponse)
async def prompt_detail_page(request: Request, prompt_id: int, version_id: int | None = None):
    ctx = get_core_ctx(request)
    repo = ctx.prompts_repo
    try:
        selected, versions = await repo.get_with_versions(ctx.db, prompt_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    if selected.archived:
        prompts = await repo.list_archived(ctx.db)
        archived_view = True
    else:
        prompts = await repo.list_active(ctx.db)
        archived_view = False
    selected_version = (
        await repo.get_version(ctx.db, version_id)
        if version_id is not None
        else _pick_default_version(versions)
    )
    return templates.TemplateResponse(
        request,
        "pages/prompts.html",
        {
            "prompts": [p.model_dump() for p in prompts],
            "selected": selected.model_dump(),
            "selected_version": _version_view(selected_version),
            "versions": [_version_view(v) for v in versions],
            "archived_view": archived_view,
            "rail_active": "prompts",
        },
    )


@router.post("/prompts/{prompt_id}/_new_version")
async def action_new_version(request: Request, prompt_id: int):
    ctx = get_core_ctx(request)
    try:
        new_vid = await ctx.prompts_repo.create_version(ctx.db, prompt_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return RedirectResponse(f"/prompts/{prompt_id}?version_id={new_vid}", status_code=303)


@router.post("/prompts/{prompt_id}/versions/{version_id}/_promote")
async def action_promote_version(request: Request, prompt_id: int, version_id: int):
    ctx = get_core_ctx(request)
    try:
        v = await ctx.prompts_repo.get_version(ctx.db, version_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    if v.prompt_id != prompt_id:
        raise HTTPException(404, "version does not belong to prompt")
    try:
        await ctx.prompts_repo.promote_version(ctx.db, prompt_id, version_id)
    except VersionImmutableError:
        pass  # silent no-op for page action; promote button only shown for drafts
    return RedirectResponse(f"/prompts/{prompt_id}?version_id={version_id}", status_code=303)


@router.post("/prompts/{prompt_id}/_duplicate")
async def action_duplicate_prompt(
    request: Request,
    prompt_id: int,
    name: str | None = Form(default=None),
    description: str | None = Form(default=None),
):
    ctx = get_core_ctx(request)
    cleaned_name = name.strip() if name is not None else None
    cleaned_desc = description if description is not None else None
    try:
        new_pid, _ = await ctx.prompts_repo.duplicate(
            ctx.db,
            prompt_id,
            name=cleaned_name or None,
            description=cleaned_desc,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except aiosqlite.IntegrityError:
        return JSONResponse(
            status_code=409,
            content={
                "error_code": "name_conflict",
                "message": f"A prompt named {cleaned_name!r} already exists.",
            },
        )
    return RedirectResponse(f"/prompts/{new_pid}", status_code=303)


@router.post("/prompts/{prompt_id}/_archive")
async def action_archive_prompt(request: Request, prompt_id: int):
    ctx = get_core_ctx(request)
    try:
        await ctx.prompts_repo.archive(ctx.db, prompt_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return RedirectResponse("/prompts", status_code=303)


@router.post("/prompts/{prompt_id}/_restore")
async def action_restore_prompt(request: Request, prompt_id: int):
    ctx = get_core_ctx(request)
    try:
        await ctx.prompts_repo.restore(ctx.db, prompt_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return RedirectResponse(f"/prompts/{prompt_id}", status_code=303)


def _pick_default_version(versions: list) -> object | None:
    """Default-displayed version: current production, fallback to latest."""
    for v in versions:
        if v.state == "production":
            return v
    return versions[0] if versions else None


def _version_view(v) -> dict:
    """Renderable dict — JSON fields stringified pretty for the textareas."""
    return {
        "id": v.id,
        "prompt_id": v.prompt_id,
        "version_num": v.version_num,
        "state": v.state,
        "body": v.body,
        "target_map_text": json.dumps(
            v.target_map.model_dump() if hasattr(v.target_map, "model_dump") else v.target_map,
            indent=2,
            ensure_ascii=False,
        ),
        "output_schema_text": json.dumps(v.output_schema, indent=2, ensure_ascii=False),
        "model": v.model,
        "created_at": v.created_at,
        "updated_at": v.updated_at,
    }
