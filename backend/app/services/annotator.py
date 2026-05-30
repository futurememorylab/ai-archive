"""Annotator service — orchestrates a job's per-clip pipeline (resolve
proxy, upload to AI store, prompt Gemini, persist annotation + review
items). Depends on ArchiveProvider, AIInputStore, GeminiService,
ProxyResolver, and the prompts/jobs/annotations/review-items repos.

For jobs with `kind='studio'`, output is persisted to studio_run instead
and the CatDV-write step is skipped entirely.
"""

import json
import logging
import mimetypes
import time
from pathlib import Path
from typing import Any

import aiosqlite

from backend.app.archive.ai_store import AIInputStore
from backend.app.models.annotation import Annotation
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from backend.app.repositories.studio_runs import StudioRunsRepo
from backend.app.services.events import EventBus
from backend.app.services.proxy_resolver import ProxyNotFound
from backend.app.services.target_map import expand

log = logging.getLogger(__name__)

JOBS_TOPIC = "jobs"


async def publish_job_progress(
    event_bus: EventBus,
    jobs_repo: JobsRepo,
    db: aiosqlite.Connection,
    job_id: int,
    *,
    status: str,
) -> None:
    """Publish job-level progress to the global `jobs` topic so the topbar
    indicator can aggregate across all active jobs."""
    done, total, errors = await jobs_repo.progress(db, job_id)
    await event_bus.publish(
        JOBS_TOPIC,
        {
            "job_id": job_id,
            "status": status,
            "done": done,
            "total": total,
            "errors": errors,
        },
    )


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
    studio_runs_repo: StudioRunsRepo,
) -> None:
    """Run a job to completion (or cancellation). Serial per job."""
    job = await jobs_repo.get_job(db, job_id)
    kind = job.kind
    version = await prompts_repo.get_version(db, job.prompt_version_id)
    await jobs_repo.update_status(db, job_id, "running")
    await publish_job_progress(event_bus, jobs_repo, db, job_id, status="running")

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
                kind=kind,
                archive=archive,
                proxy_resolver=proxy_resolver,
                ai_store=ai_store,
                gemini=gemini,
                annotations_repo=annotations_repo,
                review_items_repo=review_items_repo,
                jobs_repo=jobs_repo,
                studio_runs_repo=studio_runs_repo,
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
            msg = str(exc) or exc.__class__.__name__
            await jobs_repo.update_item_status(db, item.id, "error", error=msg)
            await event_bus.publish(
                topic, {"item_id": item.id, "status": "error", "error": msg}
            )
            # Studio runs need a terminal status of their own — the frontend
            # polls /api/studio/runs/{id} and waits for status != pending|running.
            # Without this, a Gemini auth failure (or any other non-ProxyNotFound
            # exception) leaves the run in "pending" forever and the UI's
            # "Running…" indicator never clears.
            if kind == "studio":
                run_id = await studio_runs_repo.find_latest_id_for_job_clip(
                    db, job_id=item.job_id, clip_id=item.catdv_clip_id
                )
                if run_id is not None:
                    await studio_runs_repo.complete_error(db, run_id, error=msg)
        await publish_job_progress(event_bus, jobs_repo, db, job_id, status="running")

    refreshed = await jobs_repo.list_items(db, job_id)
    final_status = "completed"
    if any(it.status == "error" for it in refreshed):
        final_status = "failed"
    if (await jobs_repo.get_job(db, job_id)).status == "cancelled":
        final_status = "cancelled"
    await jobs_repo.update_status(db, job_id, final_status)
    await publish_job_progress(event_bus, jobs_repo, db, job_id, status=final_status)


async def _process_item(
    *,
    db, item, version, kind,
    archive, proxy_resolver, ai_store, gemini,
    annotations_repo, review_items_repo,
    jobs_repo, studio_runs_repo: StudioRunsRepo,
    event_bus, topic,
) -> None:
    clip_key = ("catdv", str(item.catdv_clip_id))

    # Fast path: if the AI store already has this clip, skip the local
    # resolver + upload entirely. Gemini reads from GCS directly via the
    # returned reference, so the local proxy isn't needed at all. GCS
    # `UploadedRef`s have no expiry, so a non-None status() is durable.
    upload = await ai_store.status(clip_key)

    if upload is None:
        # Cache miss in AI store → need the local file to upload it.
        await jobs_repo.update_item_status(db, item.id, "resolving")
        await event_bus.publish(topic, {"item_id": item.id, "status": "resolving"})
        try:
            local_path: Path = await proxy_resolver.path_for_clip_id(item.catdv_clip_id)
        except ProxyNotFound:
            msg = (
                f"clip {item.catdv_clip_id} is not locally cached and not in "
                f"AI store — cache the clip on /clips first, or reconnect to CatDV"
            )
            await jobs_repo.update_item_status(db, item.id, "error", error=msg)
            await event_bus.publish(
                topic, {"item_id": item.id, "status": "error", "error": msg}
            )
            if kind == "studio":
                run_id = await studio_runs_repo.find_latest_id_for_job_clip(
                    db, job_id=item.job_id, clip_id=item.catdv_clip_id
                )
                if run_id is not None:
                    await studio_runs_repo.complete_error(db, run_id, error=msg)
            return

        await jobs_repo.update_item_status(db, item.id, "uploading")
        await event_bus.publish(topic, {"item_id": item.id, "status": "uploading"})
        mime = mimetypes.guess_type(str(local_path))[0] or "video/quicktime"
        upload = await ai_store.ensure_uploaded(clip_key, local_path, mime)

    file_ref = await ai_store.reference_for_gemini(upload)

    canonical = await archive.get_clip(str(item.catdv_clip_id))
    clip_snapshot: dict[str, Any] = dict(canonical.provider_data)
    duration_secs = float(canonical.duration_secs or 0.0)

    await jobs_repo.update_item_status(db, item.id, "prompting")
    await event_bus.publish(topic, {"item_id": item.id, "status": "prompting"})
    rendered_body = _render_prompt(version.body, duration_secs=duration_secs)
    t0 = time.monotonic()
    result = gemini.annotate(
        file_ref=file_ref,
        prompt=rendered_body,
        schema=version.output_schema,
        model=version.model,
    )
    elapsed_s = time.monotonic() - t0

    structured: dict[str, Any] | None
    try:
        structured = json.loads(result["text"]) if result.get("text") else None
    except json.JSONDecodeError:
        structured = None

    if kind == "studio":
        await _finalize_studio(
            db, item, version, structured, result, elapsed_s, duration_secs,
            studio_runs_repo, review_items_repo, jobs_repo, event_bus, topic,
        )
    else:
        await _finalize_annotation(
            db, item, version, structured, result, rendered_body,
            clip_snapshot, duration_secs,
            annotations_repo, review_items_repo, jobs_repo,
            event_bus, topic,
        )


async def _finalize_studio(
    db, item, version, structured, result, elapsed_s, duration_secs,
    studio_runs_repo: StudioRunsRepo, review_items_repo, jobs_repo,
    event_bus, topic,
) -> None:
    """Studio path: persist to studio_run + review_items (linked by
    studio_run_id), skip annotations. The studio UI renders from
    review_items through the same panels pipeline clip_detail uses."""
    run_id = await studio_runs_repo.find_latest_id_for_job_clip(
        db, job_id=item.job_id, clip_id=item.catdv_clip_id
    )
    if run_id is None:
        await jobs_repo.update_item_status(db, item.id, "error", error="studio_run not found")
        await event_bus.publish(
            topic, {"item_id": item.id, "status": "error", "error": "studio_run not found"}
        )
        return

    usage = (result.get("raw") or {}).get("usageMetadata") or {}
    tokens_in = int(usage.get("promptTokenCount", 0) or 0)
    tokens_out = int(usage.get("candidatesTokenCount", 0) or 0)
    cost_usd = 0.0  # cost calc lives elsewhere; not implemented in v1

    if structured is None:
        await studio_runs_repo.complete_error(db, run_id, error="model returned non-JSON or empty")
        await jobs_repo.update_item_status(db, item.id, "error", error="non-JSON output")
        await event_bus.publish(
            topic, {"item_id": item.id, "status": "error", "error": "non-JSON output"}
        )
        return

    # Order matters: insert review_items BEFORE complete_ok. If
    # bulk_insert raises, the outer exception handler in run_job calls
    # complete_error on this same studio_run — if we'd already marked it
    # 'ok', that would overwrite a successful run with status='error'.
    #
    # Also delete any pre-existing review_items for this run first so a
    # retry (job_item picked up again after restart / error) doesn't
    # accumulate duplicate markers/fields on the same studio_run_id.
    review = expand(
        structured,
        version.target_map,
        studio_run_id=run_id,
        catdv_clip_id=item.catdv_clip_id,
        clip_duration_secs=duration_secs or None,
    )
    await review_items_repo.delete_for_studio_run(db, studio_run_id=run_id)
    if review:
        await review_items_repo.bulk_insert(db, review)

    await studio_runs_repo.complete_ok(
        db, run_id,
        output_json=structured,
        duration_s=elapsed_s,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost_usd,
    )

    await jobs_repo.update_item_status(db, item.id, "review_ready")
    await event_bus.publish(
        topic, {"item_id": item.id, "status": "review_ready", "studio_run_id": run_id}
    )


async def _finalize_annotation(
    db, item, version, structured, result, rendered_body,
    clip_snapshot, duration_secs,
    annotations_repo, review_items_repo, jobs_repo,
    event_bus, topic,
) -> None:
    """Original annotation path: write to annotations + review_items."""
    annotation_id = await annotations_repo.insert(
        db,
        Annotation(
            catdv_clip_id=item.catdv_clip_id,
            catdv_clip_name=clip_snapshot.get("name", ""),
            prompt_version_id=version.id,
            job_id=item.job_id,
            model=version.model,
            prompt_used=rendered_body,
            raw_response=result.get("raw", {}),
            structured_output=structured,
            clip_snapshot=clip_snapshot,
        ),
    )
    await jobs_repo.attach_annotation(db, item.id, annotation_id)

    if structured:
        review = expand(
            structured,
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
