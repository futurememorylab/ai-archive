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
