"""Studio page + HTMX partial routes (PR1 — page scaffold only).

Subsequent tasks add HTMX partial endpoints (folders, clips, archive
picker, run output, player).
"""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from backend.app.deps import get_ctx
from backend.app.routes.pages.templates import templates

router = APIRouter(tags=["pages"])


@router.get("/studio", response_class=HTMLResponse)
async def studio_page(
    request: Request,
    prompt_id: int | None = None,
    version_id: int | None = None,
    compare_version_id: int | None = None,
):
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
        first_id = prompts[0].id
        assert first_id is not None
        selected_prompt, versions = await ctx.prompts_repo.get_with_versions(
            ctx.db, first_id
        )

    version_ids = {v.id for v in versions}

    # Pick the active version (cur).
    active_version = None
    if version_id is not None and version_id in version_ids:
        active_version = next(v for v in versions if v.id == version_id)
    elif versions:
        active_version = next((v for v in versions if v.state == "draft"), versions[0])

    # Pick the compare version (cmp). Skip when it equals cur (no point comparing
    # a version with itself).
    compare_version = None
    if (
        compare_version_id is not None
        and compare_version_id in version_ids
        and active_version is not None
        and compare_version_id != active_version.id
    ):
        compare_version = next(v for v in versions if v.id == compare_version_id)

    return templates.TemplateResponse(
        request,
        "pages/studio.html",
        {
            "prompts": [p.model_dump() for p in prompts],
            "selected_prompt": selected_prompt.model_dump() if selected_prompt else None,
            "versions": [v.model_dump() for v in versions],
            "active_version": active_version.model_dump() if active_version else None,
            "compare_version": compare_version.model_dump() if compare_version else None,
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


@router.get("/studio/_archive_picker", response_class=HTMLResponse)
async def _studio_archive_picker(
    request: Request,
    folder_id: int,
    q: str = "",
):
    """Renders the archive picker modal body. Uses ArchiveProvider.list_clips
    when wired; in offline/test mode, returns an empty list and the modal
    still opens (user can search again later)."""
    from backend.app.archive.model import ClipQuery

    ctx = get_ctx(request)
    results = []
    if ctx.archive:
        try:
            page = await ctx.archive.list_clips(
                str(ctx.settings.catdv_catalog_id),
                ClipQuery(text=q or None, offset=0, limit=50),
            )
            # page.items is a tuple of CanonicalClip; key[1] is the string clip id
            results = [
                {"id": int(clip.key[1]), "name": clip.name}
                for clip in (page.items or ())
            ]
        except Exception:  # noqa: BLE001
            results = []
    return templates.TemplateResponse(
        request,
        "pages/_studio_archive_picker.html",
        {"folder_id": folder_id, "q": q, "results": results},
    )


async def _build_overlay_row(
    ctx, clip_id: int, version_id: int, *, cls: str
) -> dict | None:
    """Resolve scenes + label for one version on one clip.

    Returns the row dict consumed by _player_overlay.html, or None if
    the version doesn't exist (we skip the row rather than emit a
    placeholder label).
    """
    try:
        v = await ctx.prompts_repo.get_version(ctx.db, version_id)
    except LookupError:
        return None
    run = await ctx.studio_runs_repo.latest_for_pair(
        ctx.db, prompt_version_id=version_id, clip_id=clip_id
    )
    scenes = list((run.output_json or {}).get("scenes") or []) if run else []
    return {
        "key": f"v{v.version_num}",
        "ranges": scenes,
        "cls": cls,
        "alpine_list": None,
        "x_show": None,
    }


@router.get("/studio/_player", response_class=HTMLResponse)
async def _studio_player(
    request: Request,
    clip_id: int,
    version_id: int | None = None,
    compare_id: int | None = None,
):
    """Player wrapper for the focused clip + scenes overlay.

    Builds rows = [cur (if version_id), cmp (if compare_id)] each carrying
    that version's latest run scenes. Empty rows are still passed so the
    overlay can short-circuit on no-scenes consistently.
    """
    ctx = get_ctx(request)

    # Resolve clip metadata via the archive when available.
    fps: float = 25.0
    duration_secs: float | None = None
    duration_smpte: str = ""
    if ctx.archive:
        try:
            clip = await ctx.archive.get_clip(str(clip_id))
            fps = float(clip.fps or 25.0)
            duration_secs = clip.duration_secs
        except Exception:  # noqa: BLE001
            pass

    rows: list[dict] = []
    if version_id is not None:
        row = await _build_overlay_row(ctx, clip_id, version_id, cls="range-cur")
        if row is not None:
            rows.append(row)
    if compare_id is not None:
        row = await _build_overlay_row(ctx, clip_id, compare_id, cls="range-cmp")
        if row is not None:
            rows.append(row)

    # Offline / test fallback: when no archive is configured we have no
    # canonical duration. Derive one from the scenes so the overlay
    # still renders proportionally. Skipped in production (archive
    # present) — there, a None duration_secs leaves the overlay empty,
    # which is preferable to a misleading timeline.
    if not duration_secs and ctx.archive is None:
        max_out = 0.0
        for row in rows:
            for m in row["ranges"]:
                out = m.get("out_secs") or m.get("in_secs", 0.0)
                if out and out > max_out:
                    max_out = float(out)
        if max_out > 0:
            duration_secs = max_out

    return templates.TemplateResponse(
        request,
        "pages/_studio_player.html",
        {
            "clip_id": clip_id,
            "fps": fps,
            "duration_secs": duration_secs,
            "duration_smpte": duration_smpte,
            "rows": rows,
        },
    )


@router.get("/studio/_prompt_card", response_class=HTMLResponse)
async def _studio_prompt_card(
    request: Request,
    side: Literal["cur", "cmp"],
    prompt_version_id: int,
    clip_id: int | None = None,
):
    """Renders one prompt-card. Used by HTMX swaps from the version chip
    and by the initial cmp materialization.

    404 on missing version. With clip_id, the Output tab pre-loads the
    run partial; without, the Output tab shows the focus-a-clip
    empty-state.
    """
    from backend.app.services.studio_panels import panels_from_studio_run

    ctx = get_ctx(request)
    try:
        version = await ctx.prompts_repo.get_version(ctx.db, prompt_version_id)
    except LookupError as exc:
        raise HTTPException(404, f"version {prompt_version_id} not found") from exc

    # Load the prompt's full version list for the picker dropdown (Task 8 uses it).
    _, versions = await ctx.prompts_repo.get_with_versions(ctx.db, version.prompt_id)

    run = None
    panels: dict | None = None
    fps = 25.0
    if clip_id is not None:
        run = await ctx.studio_runs_repo.latest_for_pair(
            ctx.db, prompt_version_id=prompt_version_id, clip_id=clip_id
        )
        if ctx.archive:
            try:
                clip = await ctx.archive.get_clip(str(clip_id))
                fps = float(clip.fps or 25.0)
            except Exception:  # noqa: BLE001
                pass
        panels = panels_from_studio_run(run, version, fps=fps)

    version_dict = version.model_dump()
    return templates.TemplateResponse(
        request,
        "pages/_studio_prompt_card.html",
        {
            "side": side,
            "active_version": version_dict,
            "version": version_dict,  # consumed by the embedded _studio_run_output include
            "versions": [v.model_dump() for v in versions],
            "clip_id": clip_id,
            "run": run.model_dump() if run else None,
            "panels": panels,
            "clip": {"fps": fps},
        },
    )


@router.get("/studio/_run", response_class=HTMLResponse)
async def _studio_run(
    request: Request,
    prompt_version_id: int,
    clip_id: int,
):
    from backend.app.services.studio_panels import panels_from_studio_run

    ctx = get_ctx(request)
    run = await ctx.studio_runs_repo.latest_for_pair(
        ctx.db, prompt_version_id=prompt_version_id, clip_id=clip_id
    )
    try:
        version = await ctx.prompts_repo.get_version(ctx.db, prompt_version_id)
    except LookupError:
        version = None

    # fps lookup for SMPTE rendering inside _anno_panels.html (best-effort).
    fps = 25.0
    if ctx.archive:
        try:
            clip = await ctx.archive.get_clip(str(clip_id))
            fps = float(clip.fps or 25.0)
        except Exception:  # noqa: BLE001
            pass

    panels = panels_from_studio_run(run, version, fps=fps)

    return templates.TemplateResponse(
        request,
        "pages/_studio_run_output.html",
        {
            "run": run.model_dump() if run else None,
            "version": version.model_dump() if version else None,
            "panels": panels,
            "clip": {"fps": fps},  # _anno_panels.html references clip.fps in its tc()
        },
    )
