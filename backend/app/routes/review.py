"""Review routes — HTTP endpoints under /api/review for listing review
items per clip, setting accept/reject decisions, and enqueuing the
upstream apply via the write queue."""

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from backend.app.context import CoreCtx
from backend.app.deps import get_core_ctx
from backend.app.routes.pages.clips import _build_draft_for_clip
from backend.app.routes.pages.templates import templates
from backend.app.services.write_queue import etag_from_snapshot, fps_from_snapshot
from backend.app.ui.view_models import draft_review_arrays


def _notify_sync(request: Request) -> None:
    """Nudge the sync engine to drain immediately, if live."""
    live = request.app.state.live_ctx
    if live is not None:
        live.sync_engine.notify()


def _author(request: Request) -> str | None:
    """Extract the current user's email for version authorship, or None."""
    user = getattr(request.state, "current_user", None)
    return getattr(user, "email", None)

router = APIRouter(prefix="/api/review", tags=["review"])

_VALID_KINDS = {"marker", "field", "note"}


class Decision(BaseModel):
    decision: str
    edited_value: Any = None


class ApplyBatch(BaseModel):
    clip_ids: list[int]
    kinds: list[str] | None = None



@router.get("/clips/{clip_id}/draft-data")
async def draft_data(request: Request, clip_id: int):
    """JSON draft arrays (markers/fields/notes with item_id + status, rejected
    excluded) for the redesigned Draft panel to (re)hydrate its Alpine state —
    e.g. after Apply or Annotate — without swapping server HTML into the
    reactive subtree."""
    ctx = get_core_ctx(request)
    draft = await _build_draft_for_clip(ctx, clip_id)
    return draft_review_arrays(draft)


@router.get("/clips/{clip_id}/items")
async def list_items_for_clip(request: Request, clip_id: int):
    ctx = get_core_ctx(request)
    items = await ctx.review_items_repo.list_by_clip(ctx.db, clip_id)
    return [it.model_dump() for it in items]


@router.post("/items/{item_id}/decision")
async def set_decision(request: Request, item_id: int, body: Decision):
    ctx = get_core_ctx(request)
    if body.decision not in ("accepted", "rejected", "pending"):
        raise HTTPException(400, "decision must be accepted|rejected|pending")
    await ctx.review_items_repo.set_decision(
        ctx.db,
        item_id,
        body.decision,
        edited_value=body.edited_value,
    )
    return {"id": item_id, "decision": body.decision}


async def _resolve_and_enqueue_clip(
    ctx: CoreCtx, clip_id: int, *, kinds: set[str] | None = None
) -> int:
    """Resolve a clip's accepted items + apply context and enqueue them.

    When `kinds` is given, only accepted items of those kinds are enqueued
    (so a kind-filtered bulk apply does not flush previously-accepted items
    of other kinds). Returns the number of ops queued."""
    accepted = await ctx.review_items_repo.list_by_clip(ctx.db, clip_id, decision="accepted")
    if kinds is not None:
        accepted = [it for it in accepted if it.kind in kinds]
    # list_by_clip mixes annotation-bound and studio-bound rows (CHECK
    # constraint guarantees exactly one). Studio outputs are local-only
    # per ADR 0036 — drop them before resolving an annotation row,
    # otherwise the next line dereferences None and the SyncEngine
    # would also try to push studio outputs upstream.
    accepted = [it for it in accepted if it.annotation_id is not None]
    if not accepted:
        return 0
    annotation = await ctx.annotations_repo.get(ctx.db, accepted[0].annotation_id)
    version = await ctx.prompts_repo.get_version(ctx.db, annotation.prompt_version_id)
    op_ids = await ctx.write_queue.enqueue_apply_for_clip(
        ctx.db,
        clip_id=clip_id,
        accepted=accepted,
        target_map=version.target_map,
        expected_etag=etag_from_snapshot(annotation.clip_snapshot),
        annotation_id=annotation.id,
        fps=fps_from_snapshot(annotation.clip_snapshot),
    )
    return len(op_ids)


@router.post("/apply-batch")
async def apply_batch(request: Request, body: ApplyBatch):
    """Accept all un-applied items of the given kinds on the given clips,
    then enqueue apply for each (the "yolo" bulk path).

    Unknown clip_ids or clips with no matching un-applied items are skipped
    silently (contribute 0); the loop is not atomic across clips (partial
    progress is recoverable via the durable pending-ops queue).
    """
    ctx = get_core_ctx(request)
    if ctx.write_queue is None:
        raise HTTPException(503, "write queue not initialized")
    kinds = set(body.kinds) if body.kinds else set(_VALID_KINDS)
    if not kinds <= _VALID_KINDS:
        raise HTTPException(400, "kinds must be a subset of marker|field|note")

    total_queued = 0
    clips_touched = 0
    for clip_id in body.clip_ids:
        pending = await ctx.review_items_repo.list_by_clip(ctx.db, clip_id, decision=None)
        to_accept = [it for it in pending if it.applied_at is None and it.kind in kinds]
        if not to_accept:
            continue
        for it in to_accept:
            await ctx.review_items_repo.set_decision(ctx.db, it.id, "accepted")
        queued = await _resolve_and_enqueue_clip(ctx, clip_id, kinds=kinds)
        if queued:
            clips_touched += 1
            total_queued += queued
    if total_queued:
        _notify_sync(request)
    return {"clips": clips_touched, "queued": total_queued}


@router.post("/clips/{clip_id}/apply")
async def apply_clip(
    request: Request,
    clip_id: int,
    hx_request: str | None = Header(None, alias="HX-Request"),
):
    """Publish accepted review items as a new clip version and enqueue upstream apply.

    The route used to PUT to CatDV synchronously. Now it writes one
    `pending_operations` row per ChangeOp (atomic with marking the
    review_items as `applied`) via PublishService, which also records an
    immutable clip_versions row. The SyncEngine flips the version live
    when CatDV confirms.

    HTMX callers (the clip-detail "Accept & apply" button, which stays on
    the page) send `HX-Request: true` and get the re-rendered draft aside
    partial back so the JS can swap it in place rather than full-reload.
    Non-HX callers (e.g. `applyAndNext`, which navigates away on success)
    get a JSON `{"version_id": ...}` body.
    """
    ctx = get_core_ctx(request)
    if ctx.write_queue is None:
        raise HTTPException(503, "write queue not initialized")
    version_id = await ctx.publish_service.publish(ctx.db, clip_id=clip_id, author=_author(request))
    if version_id is not None:
        _notify_sync(request)
    if hx_request == "true":
        # Re-render the draft aside so its applied/decision state reflects
        # the just-enqueued apply. `clip=None` mirrors the existing
        # GET /clips/{id}/draft partial route; _anno_draft.html guards the
        # clip.* access. Published panels are NOT re-rendered here: the
        # upstream apply runs asynchronously via the write queue, so they
        # would not have changed at this point anyway.
        draft = await _build_draft_for_clip(ctx, clip_id)
        return templates.TemplateResponse(
            request, "pages/_anno_draft.html", {"draft": draft, "clip": None}
        )
    return {"version_id": version_id}


@router.get("/clips/{clip_id}/versions")
async def list_versions(request: Request, clip_id: int):
    """Return all clip versions for a clip, ordered by version_num."""
    ctx = get_core_ctx(request)
    versions = await ctx.clip_versions_repo.list_by_clip(ctx.db, clip_id)
    return [v.model_dump() for v in versions]


@router.post("/clips/{clip_id}/versions/{version_num}/restore")
async def restore_version(request: Request, clip_id: int, version_num: int):
    """Restore a published version's snapshot back into the working draft as
    fresh pending review_items. Does not publish — call restore-and-publish
    to atomically restore + publish as a new version."""
    ctx = get_core_ctx(request)
    try:
        n = await ctx.restore_service.restore_into_draft(ctx.db, clip_id=clip_id, version_num=version_num)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"restored_items": n}


@router.post("/clips/{clip_id}/versions/{version_num}/restore-and-publish")
async def restore_and_publish(request: Request, clip_id: int, version_num: int):
    """Restore a version's snapshot and immediately publish it forward as a new
    version with origin='restore'. History is never mutated — a new row is inserted."""
    ctx = get_core_ctx(request)
    try:
        await ctx.restore_service.restore_into_draft(ctx.db, clip_id=clip_id, version_num=version_num)
        for it in await ctx.review_items_repo.list_by_clip(ctx.db, clip_id, decision="pending"):
            await ctx.review_items_repo.set_decision(ctx.db, it.id, "accepted")
        version_id = await ctx.publish_service.publish(
            ctx.db, clip_id=clip_id, author=_author(request), origin="restore"
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    _notify_sync(request)
    return {"published_version_id": version_id}
