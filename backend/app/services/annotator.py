import hashlib
import json
import logging
import mimetypes
from pathlib import Path
from typing import Any

import aiosqlite

from backend.app.models.annotation import Annotation
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.gcs_files import GcsFilesRepo
from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from backend.app.repositories.templates import TemplatesRepo
from backend.app.services.events import EventBus
from backend.app.services.target_map import expand

log = logging.getLogger(__name__)


async def run_job(
    *,
    db: aiosqlite.Connection,
    job_id: int,
    catdv,
    proxy_resolver,
    gcs,
    gemini,
    event_bus: EventBus,
    gcs_files_repo: GcsFilesRepo,
    annotations_repo: AnnotationsRepo,
    review_items_repo: ReviewItemsRepo,
    jobs_repo: JobsRepo,
    templates_repo: TemplatesRepo,
) -> None:
    """Run a job to completion (or cancellation). Serial per job."""
    job = await jobs_repo.get_job(db, job_id)
    template = await templates_repo.get(db, job.template_id)
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
                db=db, item=item, template=template, catdv=catdv,
                proxy_resolver=proxy_resolver, gcs=gcs, gemini=gemini,
                gcs_files_repo=gcs_files_repo, annotations_repo=annotations_repo,
                review_items_repo=review_items_repo, jobs_repo=jobs_repo,
                event_bus=event_bus, topic=topic,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("job %s clip %s failed", job_id, item.catdv_clip_id,
                          extra={"job_id": job_id, "clip_id": item.catdv_clip_id})
            await jobs_repo.update_item_status(
                db, item.id, "error", error=str(exc)
            )
            await event_bus.publish(topic, {"item_id": item.id, "status": "error",
                                              "error": str(exc)})

    refreshed = await jobs_repo.list_items(db, job_id)
    final_status = "completed"
    if any(it.status == "error" for it in refreshed):
        final_status = "failed"
    if (await jobs_repo.get_job(db, job_id)).status == "cancelled":
        final_status = "cancelled"
    await jobs_repo.update_status(db, job_id, final_status)


async def _process_item(
    *, db, item, template, catdv, proxy_resolver, gcs, gemini,
    gcs_files_repo, annotations_repo, review_items_repo, jobs_repo,
    event_bus, topic,
) -> None:
    await jobs_repo.update_item_status(db, item.id, "resolving")
    await event_bus.publish(topic, {"item_id": item.id, "status": "resolving"})
    local_path: Path = await proxy_resolver.path_for_clip_id(item.catdv_clip_id)

    await jobs_repo.update_item_status(db, item.id, "uploading")
    await event_bus.publish(topic, {"item_id": item.id, "status": "uploading"})

    sha = _sha256(local_path)
    existing = await gcs_files_repo.get(db, item.catdv_clip_id)
    if existing and existing["sha256"] == sha:
        gcs_uri = existing["gcs_uri"]
        await gcs_files_repo.touch(db, item.catdv_clip_id)
    else:
        mime = mimetypes.guess_type(str(local_path))[0] or "video/quicktime"
        gcs_uri = gcs.upload_if_absent(
            clip_id=item.catdv_clip_id, local_path=local_path, mime=mime,
        )
        await gcs_files_repo.upsert(
            db, clip_id=item.catdv_clip_id, gcs_uri=gcs_uri,
            mime_type=mime, size_bytes=local_path.stat().st_size, sha256=sha,
        )

    clip_snapshot: dict[str, Any] = await catdv.get_clip(item.catdv_clip_id)

    await jobs_repo.update_item_status(db, item.id, "prompting")
    await event_bus.publish(topic, {"item_id": item.id, "status": "prompting"})
    mime = mimetypes.guess_type(str(local_path))[0] or "video/quicktime"
    result = gemini.annotate(
        gcs_uri=gcs_uri, mime=mime, prompt=template.prompt,
        schema=template.output_schema, model=template.model,
    )

    structured: dict[str, Any] | None
    try:
        structured = json.loads(result["text"]) if result.get("text") else None
    except json.JSONDecodeError:
        structured = None

    annotation_id = await annotations_repo.insert(
        db,
        Annotation(
            catdv_clip_id=item.catdv_clip_id,
            catdv_clip_name=clip_snapshot.get("name", ""),
            template_id=template.id,
            job_id=item.job_id,
            model=template.model,
            prompt_used=template.prompt,
            raw_response=result.get("raw", {}),
            structured_output=structured,
            clip_snapshot=clip_snapshot,
        ),
    )
    await jobs_repo.attach_annotation(db, item.id, annotation_id)

    if structured:
        review = expand(
            structured, template.target_map,
            annotation_id=annotation_id, catdv_clip_id=item.catdv_clip_id,
        )
        if review:
            await review_items_repo.bulk_insert(db, review)

    await jobs_repo.update_item_status(db, item.id, "review_ready")
    await event_bus.publish(topic, {"item_id": item.id, "status": "review_ready",
                                      "annotation_id": annotation_id})


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
