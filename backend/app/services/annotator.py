"""Annotator service — orchestrates a job's per-clip pipeline (resolve
proxy, upload to AI store, prompt Gemini, persist annotation + review
items). Depends on ArchiveProvider, AIInputStore, GeminiService,
ProxyResolver, and the prompts/jobs/annotations/review-items repos."""

import json
import logging
import mimetypes
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

import aiosqlite

from backend.app.archive.ai_store import AIInputStore
from backend.app.models.annotation import Annotation
from backend.app.models.studio import AnnotationOutput
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from backend.app.services.events import EventBus
from backend.app.services.target_map import expand

log = logging.getLogger(__name__)


PipelineStatus = Literal["resolving", "uploading", "prompting"]


def _render_prompt(body: str, *, duration_secs: float) -> str:
    """Prepend a hard duration anchor so Gemini doesn't fabricate timestamps
    past the end of the clip — a known failure mode of gemini-2.5-flash on
    multi-minute video. Belt-and-suspenders alongside post-hoc clamping in
    `target_map.expand`."""
    if duration_secs <= 0:
        return body
    anchor = (
        f"TIMECODE UNITS — read carefully:\n"
        f"• This clip is exactly {duration_secs:.2f} seconds long.\n"
        f"• Every `in.secs` and `out.secs` you return is a FLOAT in SECONDS,\n"
        f"  measured from the start of the clip (t = 0.00 at frame 0).\n"
        f"• Do NOT use frames, milliseconds, fractions of duration, or any\n"
        f"  other unit. Decimal seconds only, e.g. 12.50 for 12 s 500 ms.\n"
        f"• Every timestamp MUST satisfy 0.0 <= secs <= {duration_secs:.2f}.\n"
        f"• Out > in for every scene; scenes must not overlap; the LAST\n"
        f"  scene's `out.secs` MUST equal the clip duration "
        f"({duration_secs:.2f}) — never exceed it.\n"
        f"• If you reach the end of the clip, STOP emitting scenes. Do not\n"
        f"  invent content beyond {duration_secs:.2f} s.\n\n"
    )
    return anchor + body


async def process_item(
    *,
    clip_resolver_arg: Any,       # int for CatDV, anything the resolver protocol accepts
    archive_lookup_arg: str | None,
    clip_key: tuple[str, str],
    version: Any,                  # PromptVersion-like; needs body, output_schema, model
    proxy_resolver: Any,
    archive: Any | None,           # may be None for uploads
    ai_store: Any,
    gemini: Any,
    on_status: Callable[[PipelineStatus], Awaitable[None]],
) -> AnnotationOutput:
    """Shared per-item Gemini pipeline. Used by both production
    `annotator.run_job` and `services/studio_runs.py`. Returns the output
    dataclass; the caller owns persistence."""

    await on_status("resolving")
    local_path: Path = await proxy_resolver.path_for_clip_id(clip_resolver_arg)

    await on_status("uploading")
    mime = mimetypes.guess_type(str(local_path))[0] or "video/quicktime"
    upload = await ai_store.ensure_uploaded(clip_key, local_path, mime)
    file_ref = await ai_store.reference_for_gemini(upload)

    duration_secs = 0.0
    if archive is not None and archive_lookup_arg is not None:
        canonical = await archive.get_clip(archive_lookup_arg)
        duration_secs = float(canonical.duration_secs or 0.0)

    await on_status("prompting")
    rendered_body = _render_prompt(version.body, duration_secs=duration_secs)
    t0 = time.monotonic()
    result = gemini.annotate(
        file_ref=file_ref,
        prompt=rendered_body,
        schema=version.output_schema,
        model=version.model,
    )
    latency_ms = int((time.monotonic() - t0) * 1000)

    raw_text = result.get("text", "") or ""
    structured: dict[str, Any] | None
    try:
        structured = json.loads(raw_text) if raw_text else None
    except json.JSONDecodeError:
        structured = None

    return AnnotationOutput(
        structured=structured,
        raw_text=raw_text,
        raw=result.get("raw", {}),
        prompt_used=rendered_body,
        model=version.model,
        latency_ms=latency_ms,
    )


async def run_job(
    *,
    db: aiosqlite.Connection,
    job_id: int,
    archive,
    proxy_resolver,
    ai_store: AIInputStore,
    gemini,
    event_bus: EventBus,
    annotations_repo: AnnotationsRepo,
    review_items_repo: ReviewItemsRepo,
    jobs_repo: JobsRepo,
    prompts_repo: PromptsRepo,
) -> None:
    """Run a job to completion (or cancellation). Serial per job."""
    job = await jobs_repo.get_job(db, job_id)
    version = await prompts_repo.get_version(db, job.prompt_version_id)
    await jobs_repo.update_status(db, job_id, "running")

    items = await jobs_repo.list_items(db, job_id)
    topic = f"job:{job_id}"

    for item in items:
        live = await jobs_repo.get_job(db, job_id)
        if live.status == "cancelled":
            log.info("job %s cancelled mid-run; stopping", job_id, extra={"job_id": job_id})
            break

        if item.status not in ("pending", "error"):
            continue

        try:
            await _process_item(
                db=db,
                item=item,
                version=version,
                archive=archive,
                proxy_resolver=proxy_resolver,
                ai_store=ai_store,
                gemini=gemini,
                annotations_repo=annotations_repo,
                review_items_repo=review_items_repo,
                jobs_repo=jobs_repo,
                event_bus=event_bus,
                topic=topic,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception(
                "job %s clip %s failed",
                job_id,
                item.catdv_clip_id,
                extra={"job_id": job_id, "clip_id": item.catdv_clip_id},
            )
            await jobs_repo.update_item_status(db, item.id, "error", error=str(exc))
            await event_bus.publish(
                topic, {"item_id": item.id, "status": "error", "error": str(exc)}
            )

    refreshed = await jobs_repo.list_items(db, job_id)
    final_status = "completed"
    if any(it.status == "error" for it in refreshed):
        final_status = "failed"
    if (await jobs_repo.get_job(db, job_id)).status == "cancelled":
        final_status = "cancelled"
    await jobs_repo.update_status(db, job_id, final_status)


async def _process_item(
    *,
    db,
    item,
    version,
    archive,
    proxy_resolver,
    ai_store,
    gemini,
    annotations_repo,
    review_items_repo,
    jobs_repo,
    event_bus,
    topic,
) -> None:
    async def on_status(s: str) -> None:
        await jobs_repo.update_item_status(db, item.id, s)
        await event_bus.publish(topic, {"item_id": item.id, "status": s})

    out = await process_item(
        clip_resolver_arg=item.catdv_clip_id,
        archive_lookup_arg=str(item.catdv_clip_id),
        clip_key=("catdv", str(item.catdv_clip_id)),
        version=version,
        proxy_resolver=proxy_resolver,
        archive=archive,
        ai_store=ai_store,
        gemini=gemini,
        on_status=on_status,
    )

    # Re-fetch canonical for the clip_snapshot persisted on the annotation row.
    # Duplicate call vs process_item is intentional — preserves byte-for-byte
    # behavior of the prior implementation. (See ADR/spec notes.)
    canonical = await archive.get_clip(str(item.catdv_clip_id))
    clip_snapshot: dict[str, Any] = dict(canonical.provider_data)
    duration_secs = float(canonical.duration_secs or 0.0)

    annotation_id = await annotations_repo.insert(
        db,
        Annotation(
            catdv_clip_id=item.catdv_clip_id,
            catdv_clip_name=clip_snapshot.get("name", ""),
            prompt_version_id=version.id,
            job_id=item.job_id,
            model=out.model,
            prompt_used=out.prompt_used,
            raw_response=out.raw,
            structured_output=out.structured,
            clip_snapshot=clip_snapshot,
        ),
    )
    await jobs_repo.attach_annotation(db, item.id, annotation_id)

    if out.structured:
        review = expand(
            out.structured,
            version.target_map,
            annotation_id=annotation_id,
            catdv_clip_id=item.catdv_clip_id,
            clip_duration_secs=duration_secs or None,
        )
        if review:
            await review_items_repo.bulk_insert(db, review)

    await jobs_repo.update_item_status(db, item.id, "review_ready")
    await event_bus.publish(
        topic, {"item_id": item.id, "status": "review_ready", "annotation_id": annotation_id}
    )
