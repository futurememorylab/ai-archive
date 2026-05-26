"""Studio page + HTMX partial routes (PR1 — page scaffold only).

Subsequent tasks add HTMX partial endpoints (folders, clips, archive
picker, run output, player).
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from backend.app.deps import get_ctx
from backend.app.routes.pages.templates import templates

router = APIRouter(tags=["pages"])


@router.get("/studio", response_class=HTMLResponse)
async def studio_page(request: Request, prompt_id: int | None = None):
    ctx = get_ctx(request)
    prompts = await ctx.prompts_repo.list_active(ctx.db)
    folders = await ctx.studio_folders_repo.list_folders_with_counts(ctx.db)

    selected_prompt = None
    versions: list = []
    if prompt_id is not None:
        try:
            selected_prompt, versions = await ctx.prompts_repo.get_with_versions(
                ctx.db, prompt_id
            )
        except LookupError:
            selected_prompt = None
            versions = []
    elif prompts:
        # Default to the first active prompt if none specified
        selected_prompt, versions = await ctx.prompts_repo.get_with_versions(
            ctx.db, prompts[0].id
        )

    # Pick the active version: the only draft if exists, else the latest
    active_version = None
    if versions:
        active_version = next((v for v in versions if v.state == "draft"), versions[0])

    return templates.TemplateResponse(
        request,
        "pages/studio.html",
        {
            "prompts": [p.model_dump() for p in prompts],
            "selected_prompt": selected_prompt.model_dump() if selected_prompt else None,
            "versions": [v.model_dump() for v in versions],
            "active_version": active_version.model_dump() if active_version else None,
            "folders": folders,
        },
    )


@router.get("/studio/_folders", response_class=HTMLResponse)
async def _studio_folders(request: Request):
    ctx = get_ctx(request)
    folders = await ctx.studio_folders_repo.list_folders_with_counts(ctx.db)
    return templates.TemplateResponse(
        request,
        "pages/_studio_folder_list.html",
        {"folders": folders, "active_version": None},
    )


@router.get("/studio/_folder", response_class=HTMLResponse)
async def _studio_folder(request: Request, folder_id: int, active_version_id: int | None = None):
    """Expanded folder view — clip cards with run-dots."""
    ctx = get_ctx(request)
    clips_rows = await ctx.studio_folders_repo.list_clips(ctx.db, folder_id)

    # Build per-clip "has any run with active version" / "any other version" flags.
    enriched = []
    for c in clips_rows:
        versions = await ctx.studio_runs_repo.versions_run_on_clip(
            ctx.db, clip_id=c["clip_id"]
        )
        has_cur = active_version_id is not None and active_version_id in versions
        has_other = any(v != active_version_id for v in versions)
        # Pull minimal clip metadata via the archive if available; fall back to id.
        meta: dict = {"name": f"clip-{c['clip_id']}", "duration_secs": None, "year": None}
        if ctx.archive:
            try:
                clip = await ctx.archive.get_clip(str(c["clip_id"]))
                meta = {
                    "name": clip.name,
                    "duration_secs": clip.duration_secs,
                    "year": (clip.provider_data or {}).get("pragafilm.rok.natoceni"),
                }
            except Exception:  # noqa: BLE001
                pass
        enriched.append({**c, **meta, "has_cur": has_cur, "has_other": has_other})

    return templates.TemplateResponse(
        request,
        "pages/_studio_folder.html",
        {"folder_id": folder_id, "clips": enriched},
    )


@router.get("/studio/_run", response_class=HTMLResponse)
async def _studio_run(
    request: Request,
    prompt_version_id: int,
    clip_id: int,
):
    ctx = get_ctx(request)
    run = await ctx.studio_runs_repo.latest_for_pair(
        ctx.db, prompt_version_id=prompt_version_id, clip_id=clip_id
    )
    try:
        version = await ctx.prompts_repo.get_version(ctx.db, prompt_version_id)
    except LookupError:
        version = None
    return templates.TemplateResponse(
        request,
        "pages/_studio_run_output.html",
        {"run": run.model_dump() if run else None, "version": version.model_dump() if version else None},
    )
