"""Studio page + HTMX partial routes (PR1 — page scaffold only).

Subsequent tasks add HTMX partial endpoints (folders, clips, archive
picker, run output, player).
"""

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from backend.app.deps import get_core_ctx
from backend.app.routes.pages.templates import templates

router = APIRouter(tags=["pages"])


def _archive(request: Request):
    """The archive provider when live, else None.

    Studio renders offline (studio outputs are local per ADR 0036), so
    archive metadata is best-effort: absent simply means no clip names /
    durations from upstream.
    """
    live = request.app.state.live_ctx
    return live.archive if live is not None else None


@router.get("/studio", response_class=HTMLResponse)
async def studio_page(
    request: Request,
    prompt_id: int | None = None,
    version_id: int | None = None,
    compare_version_id: int | None = None,
    clip_id: int | None = None,
):
    ctx = get_core_ctx(request)
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

    # Find the folder that holds the focused clip so the sidebar can
    # auto-expand it on load — otherwise after a prompt switch the player
    # restores but the clip's card is buried inside a collapsed folder,
    # which looks like focus was lost.
    focused_folder_id: int | None = None
    if clip_id is not None:
        focused_folder_id = await ctx.studio_folders_repo.folder_id_for_clip(
            ctx.db, clip_id
        )

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
            "focused_clip_id": clip_id,
            "focused_folder_id": focused_folder_id,
        },
    )


@router.get("/studio/_folders", response_class=HTMLResponse)
async def _studio_folders(request: Request):
    ctx = get_core_ctx(request)
    folders = await ctx.studio_folders_repo.list_folders_with_counts(ctx.db)
    return templates.TemplateResponse(
        request,
        "pages/_studio_folder_list.html",
        {"folders": folders, "active_version": None},
    )


@router.get("/studio/_folder", response_class=HTMLResponse)
async def _studio_folder(
    request: Request,
    folder_id: int,
    active_version_id: int | None = None,
    clip_id: int | None = None,
):
    """Expanded folder view — clip cards with run-dots.

    `clip_id` (when provided) is the currently-focused clip — used so the
    matching card renders with the `.selected` class from the start,
    instead of relying on a JS post-swap pass.
    """
    ctx = get_core_ctx(request)
    archive = _archive(request)
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
        if archive is not None:
            try:
                clip = await archive.get_clip(str(c["clip_id"]))
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
        {"folder_id": folder_id, "clips": enriched, "focused_clip_id": clip_id},
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

    ctx = get_core_ctx(request)
    archive = _archive(request)
    results = []
    if archive is not None:
        try:
            page = await archive.list_clips(
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
    """Resolve scenes + label for one version on one clip, sourced from
    review_items (not raw output_json) so the timeline overlay matches
    the Output card."""
    try:
        v = await ctx.prompts_repo.get_version(ctx.db, version_id)
    except LookupError:
        return None
    run = await ctx.studio_runs_repo.latest_for_pair(
        ctx.db, prompt_version_id=version_id, clip_id=clip_id
    )
    from backend.app.services.draft_view import _marker_from_review

    ranges: list[dict] = []
    if run is not None and run.id is not None:
        items = await ctx.review_items_repo.list_by_studio_run(ctx.db, run.id)
        # Reuse the same marker shape adapter the Output card uses, so
        # the overlay's timecode/name parsing can't drift from the
        # panel's. Drop entries that parsed to in_secs=0 only when the
        # underlying proposed_value lacked usable in.secs — but
        # target_map._filter_markers already enforced that at write
        # time, so list_by_studio_run never returns a missing-secs row.
        for it in items:
            if it.kind != "marker":
                continue
            m = _marker_from_review(it)
            ranges.append({
                "in_secs": m["in_secs"],
                "out_secs": m["out_secs"],
                "name": m["name"],
            })
    return {
        "key": f"v{v.version_num}",
        "ranges": ranges,
        "cls": cls,
        "alpine_list": None,
        "x_show": None,
    }


async def _overlay_rows_from_compare(
    ctx, clip_id: int, *, cur_version_id: int, cmp_version_id: int, archive
) -> list[dict]:
    """Build both timeline rows from the shared compare model so each range
    carries the same scene_key + status as the compare table."""
    from backend.app.services.output_compare import build_output_compare

    try:
        cur_v = await ctx.prompts_repo.get_version(ctx.db, cur_version_id)
        cmp_v = await ctx.prompts_repo.get_version(ctx.db, cmp_version_id)
    except LookupError:
        return []
    _, cur_panels, _ = await _load_studio_panels(
        ctx, version=cur_v, clip_id=clip_id, archive=archive
    )
    _, cmp_panels, _ = await _load_studio_panels(
        ctx, version=cmp_v, clip_id=clip_id, archive=archive
    )
    model = build_output_compare(cur_panels, cmp_panels)
    cur_ranges: list[dict] = []
    cmp_ranges: list[dict] = []
    for row in model["scenes"]:
        if row["cur"]:
            cur_ranges.append({
                "in_secs": row["cur"]["in_secs"], "out_secs": row["cur"]["out_secs"],
                "name": row["cur"]["name"], "scene_key": row["key"],
                "status": row["status"],
            })
        if row["cmp"]:
            cmp_ranges.append({
                "in_secs": row["cmp"]["in_secs"], "out_secs": row["cmp"]["out_secs"],
                "name": row["cmp"]["name"], "scene_key": row["key"],
                "status": row["status"],
            })
    return [
        {"key": f"v{cur_v.version_num}", "ranges": cur_ranges,
         "cls": "range-cur", "alpine_list": None, "x_show": None},
        {"key": f"v{cmp_v.version_num}", "ranges": cmp_ranges,
         "cls": "range-cmp", "alpine_list": None, "x_show": None},
    ]


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
    ctx = get_core_ctx(request)
    archive = _archive(request)

    # Resolve clip metadata via the archive when available.
    fps: float = 25.0
    duration_secs: float | None = None
    duration_smpte: str = ""
    if archive is not None:
        try:
            clip = await archive.get_clip(str(clip_id))
            fps = float(clip.fps or 25.0)
            duration_secs = clip.duration_secs
        except Exception:  # noqa: BLE001
            pass

    rows: list[dict] = []
    if version_id is not None and compare_id is not None:
        rows = await _overlay_rows_from_compare(
            ctx, clip_id, cur_version_id=version_id,
            cmp_version_id=compare_id, archive=archive,
        )
    else:
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
    if not duration_secs and archive is None:
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


async def _load_studio_panels(
    ctx, *, version, clip_id: int, archive=None
) -> tuple[Any, dict, float]:
    """Resolve (latest run, panels dict, fps) for a (version, clip) pair.

    Shared by `_studio_run` and `_studio_prompt_card` — both routes need
    the same triple, and the load was duplicated nearly verbatim before.
    Returns (run, panels, fps); `run` may be None when no run exists,
    in which case `panels` is an empty-shaped draft view. `archive` is
    optional — fps falls back to 25.0 when absent (offline)."""
    from backend.app.services.draft_view import build_draft_view

    run = await ctx.studio_runs_repo.latest_for_pair(
        ctx.db, prompt_version_id=version.id, clip_id=clip_id
    )
    fps = 25.0
    if archive is not None:
        try:
            clip = await archive.get_clip(str(clip_id))
            fps = float(clip.fps or 25.0)
        except Exception:  # noqa: BLE001
            pass
    items = (
        await ctx.review_items_repo.list_by_studio_run(ctx.db, run.id)
        if run is not None and run.id is not None
        else []
    )
    panels = build_draft_view(
        annotation=None,
        review_items=items,
        version_num=version.version_num,
        created_at=run.finished_at if run else None,
        fps=fps,
    )
    return run, panels, fps


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
    ctx = get_core_ctx(request)
    archive = _archive(request)
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
        run, panels, fps = await _load_studio_panels(
            ctx, version=version, clip_id=clip_id, archive=archive
        )

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


@router.get("/studio/_compare", response_class=HTMLResponse)
async def _studio_compare(
    request: Request,
    version_id: int,
    compare_id: int,
    clip_id: int | None = None,
):
    """Aligned scene compare table for (cur=version_id, cmp=compare_id) on a clip."""
    ctx = get_core_ctx(request)
    if clip_id is None:
        return templates.TemplateResponse(
            request, "pages/_studio_compare_table.html", {"model": None}
        )
    try:
        cur_v = await ctx.prompts_repo.get_version(ctx.db, version_id)
        cmp_v = await ctx.prompts_repo.get_version(ctx.db, compare_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="version not found") from exc
    archive = _archive(request)
    _, cur_panels, _ = await _load_studio_panels(
        ctx, version=cur_v, clip_id=clip_id, archive=archive
    )
    _, cmp_panels, _ = await _load_studio_panels(
        ctx, version=cmp_v, clip_id=clip_id, archive=archive
    )
    from backend.app.services.output_compare import build_output_compare

    model = build_output_compare(cur_panels, cmp_panels)
    return templates.TemplateResponse(
        request,
        "pages/_studio_compare_table.html",
        {
            "model": model,
            "cur_version_num": cur_v.version_num,
            "cmp_version_num": cmp_v.version_num,
        },
    )


@router.get("/studio/_run", response_class=HTMLResponse)
async def _studio_run(
    request: Request,
    prompt_version_id: int,
    clip_id: int,
):
    ctx = get_core_ctx(request)
    archive = _archive(request)
    try:
        version = await ctx.prompts_repo.get_version(ctx.db, prompt_version_id)
    except LookupError:
        version = None

    if version is None:
        # Render the empty-state branch in the template; no run/panels
        # to resolve without a version.
        return templates.TemplateResponse(
            request,
            "pages/_studio_run_output.html",
            {"run": None, "version": None, "panels": None, "clip": {"fps": 25.0}},
        )

    run, panels, fps = await _load_studio_panels(
        ctx, version=version, clip_id=clip_id, archive=archive
    )

    return templates.TemplateResponse(
        request,
        "pages/_studio_run_output.html",
        {
            "run": run.model_dump() if run else None,
            "version": version.model_dump(),
            "panels": panels,
            "clip": {"fps": fps},  # _anno_panels.html references clip.fps in its tc()
        },
    )
