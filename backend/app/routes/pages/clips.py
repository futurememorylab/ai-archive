"""Clip-facing HTML pages: list, detail, draft partial, and live-history."""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from backend.app.archive.errors import ProviderError, is_provider_not_found
from backend.app.archive.model import CanonicalClip, ClipQuery
from backend.app.deps import get_core_ctx, get_live_ctx
from backend.app.repositories.live_sessions import LiveSessionsRepo
from backend.app.routes.pages.templates import templates
from backend.app.services.clip_list_filters import (
    is_active as filters_active,
)
from backend.app.services.clip_list_filters import (
    normalize_anno,
    normalize_cache,
)
from backend.app.services.clip_list_filters import (
    resolve as resolve_filters,
)
from backend.app.timecode import secs_to_smpte
from backend.app.ui.pagination import page_offsets
from backend.app.ui.view_models import clip_detail, clip_summary, draft_review_arrays


def _humanize_age(fetched_at_iso: str | None) -> str | None:
    if not fetched_at_iso:
        return None
    try:
        ts = datetime.fromisoformat(fetched_at_iso)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    delta = datetime.now(UTC) - ts
    secs = int(delta.total_seconds())
    if secs < 5:
        return "just now"
    if secs < 60:
        return f"{secs}s ago"
    mins = secs // 60
    if mins < 60:
        return f"{mins}m ago"
    hours = mins // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


# Maps a job_item status to a clip-row batch pill (label, pill state class).
# States: "" neutral, "accent" in-flight, "ok" green, "bad" red.
_BATCH_STATUS_VIEW: dict[str, tuple[str, str]] = {
    "pending": ("Queued", ""),
    "resolving": ("Processing", "accent"),
    "uploading": ("Processing", "accent"),
    "prompting": ("Processing", "accent"),
    "annotated": ("Done", "ok"),
    "review_ready": ("Done", "ok"),
    "applied": ("Applied", "ok"),
    "rejected": ("Rejected", ""),
    "error": ("Failed", "bad"),
}


# Item statuses that mean "still working" (maps to the Queued/Processing pills).
# Mirrors the in-flight set in JobsRepo._BATCHES_SQL.
_RUNNING_ITEM_STATUSES = frozenset(
    {"pending", "resolving", "uploading", "prompting"}
)


def _batch_status_view(status: str | None) -> dict[str, str] | None:
    if status is None:
        return None
    label, state = _BATCH_STATUS_VIEW.get(status, (status, ""))
    return {"label": label, "state": state}


def _batch_options(jobs: list) -> list[dict[str, str]]:
    """Batch-filter dropdown entries. The per-kind jobs of one bulk action
    share a run_group and collapse into a single entry (value = all their job
    ids); single-clip / studio jobs stay individual."""
    by_group: dict[str, list] = {}
    for j in jobs:
        if j.run_group:
            by_group.setdefault(j.run_group, []).append(j)

    options: list[dict[str, str]] = []
    seen: set[str] = set()
    for j in jobs:
        if j.run_group:
            if j.run_group in seen:
                continue
            seen.add(j.run_group)
            members = by_group[j.run_group]
            ids = sorted(int(m.id) for m in members)
            total = sum(m.total_clips for m in members)
            options.append(
                {
                    "value": ",".join(str(i) for i in ids),
                    "label": "#" + "+".join(str(i) for i in ids) + f" · bulk ({total})",
                }
            )
        else:
            label = f"#{j.id}"
            if j.notes:
                label += f" · {j.notes}"
            elif j.kind:
                label += f" · {j.kind}"
            label += f" ({j.total_clips})"
            options.append({"value": str(j.id), "label": label})
    return options


logger = logging.getLogger(__name__)

router = APIRouter(tags=["pages"])


async def query_clip_page(
    ctx,
    *,
    catalog_id: str,
    q: str | None,
    offset: int,
    limit: int,
    cache_f,
    anno_f,
    batch_ids: list[int],
    host_local_proxies: bool,
) -> tuple[list[dict], int, str | None]:
    """Shared clip-page query for the clips list and the batch picker.

    Returns (clip_summary rows, total, cache_fetched_at). Encapsulates the
    host-local cache collapse, the filtered-vs-plain page fetch, the bulk
    cache-status lookup, and per-clip clip_summary. Raises ProviderError on
    archive failure (callers map to 502)."""
    # In host-local mode `cache=local` matches every clip — collapse to "any".
    effective_cache_f = "any" if (host_local_proxies and cache_f == "local") else cache_f
    cache_fetched_at: str | None = None

    if filters_active(effective_cache_f, anno_f, batch_ids):
        clips, total = await _filtered_page(
            ctx,
            catalog_id=catalog_id,
            q=q,
            offset=offset,
            limit=limit,
            cache_filter=effective_cache_f,
            anno_filter=anno_f,
            host_local_proxies=host_local_proxies,
            batch=batch_ids,
        )
    else:
        page = await ctx.archive.list_clips(
            catalog_id, ClipQuery(text=q, offset=offset, limit=limit)
        )
        clips = list(page.items)
        total = page.total
        entry = await ctx.clip_list_cache_repo.get(
            ctx.db,
            provider_id="catdv",
            catalog_id=catalog_id,
            query_text=q,
            offset=offset,
            limit=limit,
        )
        cache_fetched_at = entry["fetched_at"] if entry is not None else None

    statuses: dict[tuple[str, str], object] = {}
    if clips:
        keys = [c.key for c in clips]
        rows = await ctx.cache_inspector.status_for_clips(keys)
        statuses = {r.clip_key: r for r in rows}

    summaries = [clip_summary(c, cache_status=statuses.get(c.key)) for c in clips]
    return summaries, total, cache_fetched_at


@router.get("/", response_class=HTMLResponse)
async def clips_list(
    request: Request,
    q: str | None = None,
    offset: int = 0,
    limit: int = 50,
    refresh: int = 0,
    cache: str | None = None,
    anno: str | None = None,
    batch: str | None = None,
):
    ctx = get_live_ctx(request)

    catalog_id = str(ctx.settings.catdv_catalog_id)
    cache_f = normalize_cache(cache)
    anno_f = normalize_anno(anno)
    # `batch` arrives as a comma-separated query string of job ids: the
    # indicator links to every per-kind job of one bulk action at once, the
    # dropdown submits a single id, and the "Any" option submits empty.
    batch_ids = [int(b) for b in (batch or "").split(",") if b.strip().isdigit()]
    # Canonical (sorted) form so it matches the grouped dropdown option values.
    batch_query = ",".join(str(b) for b in sorted(batch_ids))
    # The dropdown's selected-state only makes sense for a single id.
    batch_id = batch_ids[0] if len(batch_ids) == 1 else None
    host_local_proxies = getattr(getattr(ctx, "proxy_resolver", None), "is_host_local", False)

    # `?refresh=1` lets the user bypass the list cache when they suspect
    # upstream changed. Wipe every cached page for this catalog so the next
    # list_clips() hits CatDV and we don't serve a stale neighbour page.
    if refresh:
        await ctx.clip_list_cache_repo.invalidate_catalog(
            ctx.db, provider_id="catdv", catalog_id=catalog_id
        )

    # In host-local mode `cache=local` matches every clip — collapse to "any"
    # so the standard CatDV-paginated path is used. `cache=none` keeps its
    # filter status (resolve_filters short-circuits to empty downstream).
    effective_cache_f = "any" if (host_local_proxies and cache_f == "local") else cache_f

    try:
        clip_rows, total, cache_fetched_at = await query_clip_page(
            ctx,
            catalog_id=catalog_id,
            q=q,
            offset=offset,
            limit=limit,
            cache_f=cache_f,
            anno_f=anno_f,
            batch_ids=batch_ids,
            host_local_proxies=host_local_proxies,
        )
    except ProviderError as exc:
        raise HTTPException(502, f"archive error: {exc}") from exc

    jobs = await ctx.jobs_repo.list_jobs(ctx.db, limit=50)
    prev_offset, next_offset = page_offsets(offset, limit, total)
    ctx_dict = {
        "q": q or "",
        "offset": offset,
        "limit": limit,
        "total": total,
        "cache_filter": cache_f,
        "anno_filter": anno_f,
        "batch_filter": batch_id,
        "batch_query": batch_query,
        "jobs": jobs,
        "batch_options": _batch_options(jobs),
        "filters_active": filters_active(effective_cache_f, anno_f, batch_ids),
        "host_local_proxies": host_local_proxies,
        "catalog": {
            "id": ctx.settings.catdv_catalog_id,
            "name": "AI katalog",
        },
        "clips": clip_rows,
        "prev_offset": prev_offset,
        "next_offset": next_offset,
        "cache_fetched_at": cache_fetched_at,
        "cache_age": _humanize_age(cache_fetched_at),
    }

    # When viewing a batch, surface each clip's per-item run status (queued /
    # processing / done / failed) from the job's items, merged across all the
    # per-kind jobs of the bulk action.
    batch_status_map: dict[int, str] = {}
    for jid in batch_ids:
        for it in await ctx.jobs_repo.list_items(ctx.db, jid):
            batch_status_map[it.catdv_clip_id] = it.status
    # While any item is still in-flight, the per-clip pills change underneath a
    # static page — flag it so the tbody self-polls and refreshes the pills
    # without a manual reload. Stops automatically once the batch settles.
    ctx_dict["batch_running"] = any(
        s in _RUNNING_ITEM_STATUSES for s in batch_status_map.values()
    )

    # Actual billable cost per clip for this batch: one batched aggregate
    # over the batch's job ids, summed per clip in SQL (a clip may appear
    # in more than one per-kind job, and retries add rows).
    batch_cost_map: dict[int, float] = {}
    if batch_ids:
        batch_cost_map = await ctx.run_telemetry_repo.cost_totals_by_clip(ctx.db, batch_ids)

    # Annotate each row with its pending-draft counts and batch job id.
    pending_rows = await ctx.review_items_repo.list_pending_clips(ctx.db, limit=2000, offset=0)
    pmap = {r["catdv_clip_id"]: r for r in pending_rows}
    for row in ctx_dict["clips"]:
        row["batch_status"] = _batch_status_view(batch_status_map.get(row["id"]))
        bc = batch_cost_map.get(row["id"])
        row["batch_cost_usd"] = bc if bc else None
        p = pmap.get(row["id"])
        mc = p["marker_count"] if p else 0
        fc = p["field_count"] if p else 0
        nc = p["note_count"] if p else 0
        parts = []
        if mc:
            parts.append(f"{mc}m")
        if fc:
            parts.append(f"{fc}f")
        if nc:
            parts.append(f"{nc}n")
        row["draft_label"] = " · ".join(parts) if parts else ""
        row["batch"] = p["job_id"] if p else None

    template = (
        "pages/_clips_tbody.html"
        if request.headers.get("HX-Request") == "true"
        else "pages/clips.html"
    )
    return templates.TemplateResponse(request, template, ctx_dict)


async def _filtered_page(
    ctx,
    *,
    catalog_id: str,
    q: str | None,
    offset: int,
    limit: int,
    cache_filter,
    anno_filter,
    host_local_proxies: bool = False,
    batch: list[int] | None = None,
) -> tuple[list[CanonicalClip], int]:
    """Local-first paginated list when any filter is active.

    Builds the candidate clip-id set from SQLite, hydrates each clip
    (preferring the metadata cache, falling back to a single CatDV fetch),
    optionally applies the text query, sorts by name for stable paging,
    then slices to the requested page.
    """
    candidate_ids = await resolve_filters(
        ctx.db,
        provider_id="catdv",
        catalog_id=catalog_id,
        cache=cache_filter,
        anno=anno_filter,
        host_local_proxies=host_local_proxies,
        batch=batch,
    )
    if not candidate_ids:
        return [], 0

    needle = (q or "").strip().casefold() or None

    # Hydrate locally: the clips were almost certainly listed already, so a
    # single read of the cached list pages avoids a per-clip CatDV round-trip
    # (the old behavior that made the Batch view slow). Falls back to the
    # per-clip resolver only for genuine misses.
    list_cache = await ctx.clip_list_cache_repo.clips_for_catalog(
        ctx.db, provider_id="catdv", catalog_id=catalog_id
    )

    hydrated: list[CanonicalClip] = []
    for cid in candidate_ids:
        clip = list_cache.get(str(cid))
        if clip is None:
            try:
                clip = await _hydrate_clip(ctx, cid)
            except ProviderError as exc:
                # CatDV offline / transient, OR a synthetic-id review_items clip
                # with no upstream record. Skip it from THIS render rather than
                # 502-ing the entire filtered list — it reappears on refresh
                # when the archive is reachable. (HTMX swallows non-2xx swaps,
                # so a 502 here silently leaves the previous filter's rows on
                # screen and makes the filter look broken.) See ADR 0087.
                logger.warning("filtered list: skipping clip %s — %s", cid, exc)
                continue
        if clip is None:
            continue
        if needle is not None and needle not in clip.name.casefold():
            continue
        hydrated.append(clip)

    hydrated.sort(key=lambda c: (c.name.casefold(), int(c.key[1])))
    total = len(hydrated)
    return hydrated[offset : offset + limit], total


async def _hydrate_clip(ctx, clip_id: int) -> CanonicalClip | None:
    """Fetch a CanonicalClip cheaply, preferring local metadata cache."""
    clip = await ctx.clip_cache_repo.get_by_key(
        ctx.db,
        provider_id="catdv",
        provider_clip_id=str(clip_id),
    )
    if clip is not None:
        return clip
    try:
        return await ctx.archive.get_clip(str(clip_id))
    except ProviderError as exc:
        # Only a genuine NOT_FOUND (stale id / removed upstream) is safe to
        # skip silently; a transient error must NOT be read as absence (it
        # would drop live clips from the filtered/batch view). See ADR 0042.
        if is_provider_not_found(exc):
            return None
        raise


async def _build_draft_for_clip(ctx, clip_id: int) -> dict:
    from backend.app.services.draft_view import build_draft_view

    annotations = await ctx.annotations_repo.list_by_clip(ctx.db, clip_id)
    if not annotations:
        return build_draft_view(annotation=None, review_items=[])
    latest = annotations[0]  # DESC order
    all_items = await ctx.review_items_repo.list_by_clip(ctx.db, clip_id)
    items = [it for it in all_items if it.annotation_id == latest.id]
    prompt_name: str | None = None
    version_num: int | None = None
    try:
        version = await ctx.prompts_repo.get_version(ctx.db, latest.prompt_version_id)
        version_num = version.version_num
        prompt, _ = await ctx.prompts_repo.get_with_versions(ctx.db, version.prompt_id)
        prompt_name = prompt.name
    except LookupError:
        pass
    return build_draft_view(
        annotation=latest,
        review_items=items,
        prompt_name=prompt_name,
        version_num=version_num,
        created_at=latest.created_at,
    )


async def _build_clip_view_model_for_live(ctx, clip_id: int) -> dict:
    """Reshape a CanonicalClip into the dict shape `build_context_text` expects:
    `fields` as identifier→raw-value dict, `markers` carrying smpte timestamps.
    """
    from backend.app.ui.view_models import _PRAGAFILM_PREFIX, _fix

    clip = await ctx.archive.get_clip(str(clip_id))
    fps = clip.fps or 25.0
    markers = []
    for m in sorted(clip.markers, key=lambda x: x.in_.secs):
        in_secs = m.in_.secs
        out_secs = m.out.secs if m.out is not None else None
        markers.append(
            {
                "name": _fix(m.name) or "",
                "description": _fix(m.description) or "",
                "in_secs": in_secs,
                "out_secs": out_secs,
                "in_smpte": secs_to_smpte(in_secs, fps),
                "out_smpte": secs_to_smpte(out_secs, fps) if out_secs is not None else "",
                "category": m.category,
            }
        )
    fields = {
        ident: fv.value for ident, fv in clip.fields.items() if ident.startswith(_PRAGAFILM_PREFIX)
    }
    return {
        "id": int(clip.key[1]),
        "name": clip.name,
        "fps": fps,
        "duration_secs": clip.duration_secs,
        "duration_smpte": secs_to_smpte(clip.duration_secs or 0, fps),
        "format": "",  # purely cosmetic in the context block; raw provider data unparsed
        "notes": clip.provider_data.get("notes") if clip.provider_data else None,
        "big_notes": clip.provider_data.get("bigNotes") if clip.provider_data else None,
        "markers": markers,
        "fields": fields,
    }


async def _build_draft_view_model_for_live(ctx, clip_id: int) -> dict:
    """Reshape the draft view into `build_context_text`'s expected shape."""
    from backend.app.ui.view_models import _fix

    annotations = await ctx.annotations_repo.list_by_clip(ctx.db, clip_id)
    if not annotations:
        return {"markers": [], "fields": {}, "notes": ""}
    latest = annotations[0]
    all_items = await ctx.review_items_repo.list_by_clip(ctx.db, clip_id)
    items = [it for it in all_items if it.annotation_id == latest.id]

    clip = await ctx.archive.get_clip(str(clip_id))
    fps = clip.fps or 25.0

    markers: list[dict] = []
    fields: dict = {}
    notes_parts: list[str] = []
    for it in items:
        if it.kind == "marker":
            pv = it.proposed_value if isinstance(it.proposed_value, dict) else {}
            in_secs = float((pv.get("in") or {}).get("secs", 0.0))
            out_part = pv.get("out") or {}
            out_secs = (
                float(out_part["secs"])
                if isinstance(out_part, dict) and "secs" in out_part
                else None
            )
            markers.append(
                {
                    "name": _fix(pv.get("name")) or "",
                    "description": _fix(pv.get("description")) or "",
                    "in_secs": in_secs,
                    "out_secs": out_secs,
                    "in_smpte": secs_to_smpte(in_secs, fps),
                    "out_smpte": secs_to_smpte(out_secs, fps) if out_secs is not None else "",
                    "category": pv.get("category"),
                }
            )
        elif it.kind == "field":
            ident = it.target_identifier or ""
            if ident:
                fields[ident] = it.proposed_value
        elif it.kind == "note":
            if it.proposed_value is not None:
                notes_parts.append(str(it.proposed_value))
    markers.sort(key=lambda m: m["in_secs"])
    return {
        "markers": markers,
        "fields": fields,
        "notes": "\n\n".join(notes_parts),
    }


@router.get("/clips/{clip_id}", response_class=HTMLResponse)
async def clip_detail_page(request: Request, clip_id: int, review: int | None = None):
    ctx = get_live_ctx(request)
    try:
        clip = await ctx.archive.get_clip(str(clip_id))
    except ProviderError as exc:
        if "not available offline" in str(exc):
            return templates.TemplateResponse(
                request,
                "pages/clip_not_cached.html",
                {"clip_id": clip_id},
                status_code=404,
            )
        raise HTTPException(404, f"clip not found: {exc}") from exc

    cache_status = await ctx.cache_inspector.status_for_clip(clip.key)

    ctx_dict = clip_detail(clip, cache_status=cache_status)
    ctx_dict["duration_smpte"] = secs_to_smpte(
        ctx_dict["clip"]["duration_secs"], ctx_dict["clip"]["fps"]
    )
    ctx_dict["draft"] = await _build_draft_for_clip(ctx, clip_id)
    ctx_dict["draft_arrays"] = draft_review_arrays(ctx_dict["draft"])
    # Actual billable cost of the published annotation (its originating job).
    # Latest annotation first (list_by_clip orders id DESC); skip live/manual
    # annotations that have no job_id (nothing was billed for them).
    ctx_dict["annotation_cost_usd"] = None
    annotations = await ctx.annotations_repo.list_by_clip(ctx.db, clip_id)
    job_id = next((a.job_id for a in annotations if a.job_id is not None), None)
    if job_id is not None:
        costs = await ctx.run_telemetry_repo.cost_totals_by_clip(ctx.db, [job_id])
        ctx_dict["annotation_cost_usd"] = costs.get(clip_id)
    ctx_dict["host_local_proxies"] = getattr(
        getattr(ctx, "proxy_resolver", None), "is_host_local", False
    )
    ctx_dict["gemini_live_inactivity_s"] = getattr(
        ctx.settings,
        "gemini_live_inactivity_s",
        60,
    )
    ctx_dict["review_mode"] = bool(review)
    return templates.TemplateResponse(request, "pages/clip_detail.html", ctx_dict)


@router.get("/clips/{clip_id}/draft", response_class=HTMLResponse)
async def clip_draft_partial(request: Request, clip_id: int):
    ctx = get_live_ctx(request)
    try:
        # Confirm the clip exists so we 404 properly; we don't render it here.
        await ctx.archive.get_clip(str(clip_id))
    except ProviderError as exc:
        raise HTTPException(404, f"clip not found: {exc}") from exc

    draft = await _build_draft_for_clip(ctx, clip_id)
    return templates.TemplateResponse(
        request, "pages/_anno_draft.html", {"draft": draft, "clip": None}
    )


@router.get("/clips/{clip_id}/published", response_class=HTMLResponse)
async def clip_published_partial(request: Request, clip_id: int):
    """Re-render the published annotation panels from fresh provider data.

    review.js fetches this after a writeback finishes (driven by the per-clip
    sync-status poll) and swaps `#published-panels` in place, so applied
    markers / fields / notes show without a full page reload. The provider's
    published cache is invalidated by a successful `apply_changes`, so this
    `get_clip` returns the just-written annotation.
    """
    ctx = get_live_ctx(request)
    try:
        clip = await ctx.archive.get_clip(str(clip_id))
    except ProviderError as exc:
        raise HTTPException(404, f"clip not found: {exc}") from exc
    # The published panels only read markers/fields/notes — not the cache badge —
    # so skip the cache_inspector lookup that clip_detail_page needs.
    ctx_dict = clip_detail(clip)
    return templates.TemplateResponse(
        request, "pages/_published_refreshable.html", {"clip": ctx_dict["clip"]}
    )


@router.get("/clips/{clip_id}/live-history", response_class=HTMLResponse)
async def clip_live_history(request: Request, clip_id: int):
    ctx = get_core_ctx(request)
    repo = LiveSessionsRepo()
    rows = await repo.list_by_clip(ctx.db, clip_id)
    sessions = []
    from datetime import datetime as _dt

    for s in rows:
        duration_s = None
        if s.started_at and s.ended_at:
            try:
                duration_s = (
                    _dt.fromisoformat(s.ended_at) - _dt.fromisoformat(s.started_at)
                ).total_seconds()
            except ValueError:
                pass
        sessions.append(
            {
                "id": s.id,
                "started_at": s.started_at,
                "created_at": s.created_at,
                "duration_s": duration_s,
                "end_reason": s.end_reason,
                "state": s.state,
                "has_summary": s.summary_cs is not None,
                "frame_count": s.frame_count,
            }
        )
    return templates.TemplateResponse(
        request,
        "pages/_anno_live_history.html",
        {"sessions": sessions},
    )


@router.get("/ui/batch-statuses", response_class=HTMLResponse)
async def batch_statuses_fragment(request: Request, batch: str = ""):
    """Per-clip run-status pills for a running batch, as HTMX out-of-band <span>
    swaps — so the clips list refreshes just the pills in place, with no
    full-table re-render (which would reset the scroll and re-run the list's
    heavier queries every few seconds). DB-only; polled by #bstatus-poll while
    the batch is running, and tells the poller to stop once it settles."""
    ctx = get_core_ctx(request)
    job_ids = [int(b) for b in batch.split(",") if b.strip().isdigit()]
    status_map: dict[int, str] = {}
    for jid in job_ids:
        for it in await ctx.jobs_repo.list_items(ctx.db, jid):
            status_map[it.catdv_clip_id] = it.status
    cells = {cid: _batch_status_view(s) for cid, s in status_map.items()}
    running = any(s in _RUNNING_ITEM_STATUSES for s in status_map.values())
    return templates.TemplateResponse(
        request,
        "pages/_batch_status_cells.html",
        {"cells": cells, "batch_running": running},
    )
