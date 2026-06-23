"""Annotator service — orchestrates a job's per-clip pipeline (resolve
proxy, upload to AI store, prompt Gemini, persist annotation + review
items). Depends on ArchiveProvider, AIInputStore, GeminiService,
ProxyResolver, and the prompts/jobs/annotations/review-items repos.

For jobs with `kind='studio'`, output is persisted to studio_run instead
and the CatDV-write step is skipped entirely.
"""

import asyncio
import json
import logging
import mimetypes
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from backend.app.archive.ai_store import AIInputStore
from backend.app.media_kind import classify_media_kind
from backend.app.models.annotation import Annotation
from backend.app.models.telemetry import RunTelemetryRecord, TelemetryCtx
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.model_config import ModelConfigRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from backend.app.repositories.run_telemetry import RunTelemetryRepo
from backend.app.repositories.studio_runs import StudioRunsRepo
from backend.app.services import run_estimator
from backend.app.services.errors import humanise as _humanise_error
from backend.app.services.events import EventBus
from backend.app.services.pricing import compute_cost
from backend.app.services.proxy_resolver import ProxyNotFound
from backend.app.services.target_map import expand
from backend.app.services.telemetry_capture import (
    extract_finish_reason,
    extract_usage,
    prompt_hash,
    schema_hash,
)
from backend.app.uploaded_ids import is_uploaded

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


@dataclass(frozen=True)
class CaptureMeta:
    """Per-run facts gathered in _process_item for the telemetry row:
    media descriptors plus the rendered-prompt length (not a media
    attribute, but captured at the same point in the pipeline). All
    fields default None so CaptureMeta() serves as the empty meta on
    error paths that never reached media resolution."""

    media_kind: str | None = None
    media_duration_secs: float | None = None
    media_fps: float | None = None
    media_bytes: int | None = None
    media_ext: str | None = None
    clip_name: str | None = None
    prompt_chars_rendered: int | None = None


async def _record_telemetry(
    db,
    repo: RunTelemetryRepo,
    tctx: TelemetryCtx,
    *,
    kind: str,
    item,
    version,
    status: str,
    result: dict | None = None,
    error_class: str | None = None,
    duration_s: float | None = None,
    capture: CaptureMeta | None = None,
    est=None,
    ai_store_kind: str | None = None,
    review_item_count: int | None = None,
    media_resolution_setting: str | None = None,
) -> None:
    """Write one run_telemetry row. Telemetry/bookkeeping must NEVER fail
    a run, so every path here is wrapped in try/except + log."""
    try:
        raw = (result or {}).get("raw") or {}
        usage = extract_usage(raw)
        cost_usd, pricing_version = compute_cost(usage, version.model)
        if status == "error":
            cost_usd = None
        rendered_len = len((result or {}).get("text") or "") if result else None
        mm = capture or CaptureMeta()
        rec = RunTelemetryRecord(
            occurred_at=datetime.now(UTC).isoformat(),
            install_id=tctx.install_id,
            app_version=tctx.app_version,
            kind="studio" if kind == "studio" else "annotation",
            archive_id=tctx.archive_id,
            job_id=item.job_id,
            clip_id=item.catdv_clip_id,
            clip_name=mm.clip_name,
            prompt_version_id=version.id,
            prompt_hash=prompt_hash(version.body),
            schema_hash=schema_hash(version.output_schema),
            prompt_chars_rendered=mm.prompt_chars_rendered,
            model=version.model,
            media_kind=mm.media_kind,
            media_duration_secs=mm.media_duration_secs,
            media_fps=mm.media_fps,
            media_bytes=mm.media_bytes,
            media_ext=mm.media_ext,
            vertex_project=tctx.vertex_project,
            vertex_location=tctx.vertex_location,
            ai_store_kind=ai_store_kind,
            status=status,
            error_class=error_class,
            finish_reason=extract_finish_reason(raw),
            attempt_count=1,
            duration_s=duration_s,
            tokens_in=usage.tokens_in,
            tokens_in_text=usage.tokens_in_text,
            tokens_in_video=usage.tokens_in_video,
            tokens_in_audio=usage.tokens_in_audio,
            tokens_in_image=usage.tokens_in_image,
            tokens_cached=usage.tokens_cached,
            tokens_out=usage.tokens_out,
            tokens_thinking=usage.tokens_thinking,
            cost_usd=cost_usd,
            pricing_version=pricing_version,
            est_tokens_in=getattr(est, "tokens_in", None),
            est_tokens_out_p50=getattr(est, "tokens_out_p50", None),
            est_tokens_out_p90=getattr(est, "tokens_out_p90", None),
            est_cost_usd_p50=getattr(est, "cost_usd_p50", None),
            est_cost_usd_p90=getattr(est, "cost_usd_p90", None),
            est_confidence=getattr(est, "confidence", None),
            output_chars=rendered_len,
            review_item_count=review_item_count,
            media_resolution_setting=media_resolution_setting,
        )
        await repo.insert(db, rec)
    except Exception:  # noqa: BLE001 — telemetry must never fail the run
        log.exception("run_telemetry insert failed (run unaffected)")


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
    uploaded_clips_repo,
    run_telemetry_repo: RunTelemetryRepo,
    telemetry_ctx: TelemetryCtx,
    model_config_repo: ModelConfigRepo,
    only_clip_ids: set[int] | None = None,
    force_resolution: str | None = None,
    record_only: bool = False,
) -> None:
    """Run a job to completion (or cancellation). Serial per job."""
    job = await jobs_repo.get_job(db, job_id)
    kind = job.kind
    version = await prompts_repo.get_version(db, job.prompt_version_id)
    await jobs_repo.update_status(db, job_id, "running")
    if kind != "studio":
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

        if only_clip_ids is not None and item.catdv_clip_id not in only_clip_ids:
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
                uploaded_clips_repo=uploaded_clips_repo,
                run_telemetry_repo=run_telemetry_repo,
                telemetry_ctx=telemetry_ctx,
                model_config_repo=model_config_repo,
                event_bus=event_bus,
                topic=topic,
                force_resolution=force_resolution,
                record_only=record_only,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception(
                "job %s clip %s failed",
                job_id,
                item.catdv_clip_id,
                extra={"job_id": job_id, "clip_id": item.catdv_clip_id},
            )
            msg = _humanise_error(exc)
            await jobs_repo.update_item_status(db, item.id, "error", error=msg)
            await event_bus.publish(topic, {"item_id": item.id, "status": "error", "error": msg})
            await _record_telemetry(
                db,
                run_telemetry_repo,
                telemetry_ctx,
                kind=kind,
                item=item,
                version=version,
                status="error",
                error_class=type(exc).__name__,
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
        if kind != "studio":
            await publish_job_progress(event_bus, jobs_repo, db, job_id, status="running")

    refreshed = await jobs_repo.list_items(db, job_id)
    final_status = "completed"
    if any(it.status == "error" for it in refreshed):
        final_status = "failed"
    if (await jobs_repo.get_job(db, job_id)).status == "cancelled":
        final_status = "cancelled"
    await jobs_repo.update_status(db, job_id, final_status)
    if kind != "studio":
        await publish_job_progress(event_bus, jobs_repo, db, job_id, status=final_status)


@dataclass
class _ClipMeta:
    clip_key: tuple[str, str]
    duration_secs: float
    media_kind: str
    clip_name: str | None
    media_fps: float | None
    media_bytes: int | None
    media_ext: str | None
    clip_snapshot: dict[str, Any]


async def _resolve_clip_meta(db, *, clip_id, archive, uploaded_clips_repo) -> _ClipMeta:
    """Resolve the per-clip metadata + AI-store key for one run item,
    branching on whether `clip_id` is an uploaded synthetic id."""
    if is_uploaded(clip_id):
        row = await uploaded_clips_repo.get(db, clip_id)
        stored = (row or {}).get("stored_filename") or ""
        return _ClipMeta(
            clip_key=("uploaded", str(clip_id)),
            duration_secs=float((row or {}).get("duration_secs") or 0.0),
            media_kind="video",  # web-safe constraint guarantees video
            clip_name=(row or {}).get("original_filename"),
            media_fps=None,
            media_bytes=(row or {}).get("size_bytes"),
            media_ext=(Path(stored).suffix.lower() or None) if stored else None,
            clip_snapshot={},
        )
    canonical = await archive.get_clip(str(clip_id))
    media_path = str((canonical.media.cached_path or canonical.media.upstream_handle) or "")
    return _ClipMeta(
        clip_key=("catdv", str(clip_id)),
        duration_secs=float(canonical.duration_secs or 0.0),
        media_kind=classify_media_kind(media_path or None),
        clip_name=canonical.name or None,
        media_fps=canonical.fps or None,
        media_bytes=canonical.media.size_bytes,
        media_ext=(Path(media_path).suffix.lower() or None) if media_path else None,
        clip_snapshot=dict(canonical.provider_data),
    )


async def _process_item(
    *,
    db,
    item,
    version,
    kind,
    archive,
    proxy_resolver,
    ai_store,
    gemini,
    annotations_repo,
    review_items_repo,
    jobs_repo,
    studio_runs_repo: StudioRunsRepo,
    uploaded_clips_repo,
    run_telemetry_repo: RunTelemetryRepo,
    telemetry_ctx: TelemetryCtx,
    model_config_repo: ModelConfigRepo,
    event_bus,
    topic,
    force_resolution: str | None = None,
    record_only: bool = False,
) -> None:
    # Compute the AI-store key cheaply (no archive call). Full per-clip
    # metadata is resolved lazily below, AFTER the proxy/AI-store cache-miss
    # short-circuit — so a clip that is in neither cache fails fast WITHOUT
    # ever calling archive.get_clip / hitting the seat-limited CatDV server.
    # This preserves the invariant guarded by
    # test_run_fails_clearly_when_neither_cached and the cache/seat discipline
    # in CLAUDE.md (the spec says the proxy/ai-store flow is "reused as-is").
    clip_key = (
        ("uploaded", str(item.catdv_clip_id))
        if is_uploaded(item.catdv_clip_id)
        else ("catdv", str(item.catdv_clip_id))
    )

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
            await event_bus.publish(topic, {"item_id": item.id, "status": "error", "error": msg})
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

    # Resolve full metadata now — only reached once the clip is known to be
    # available (proxy resolved or already in the AI store). For archive
    # clips this is where the single archive.get_clip call happens; uploaded
    # clips read their row from UploadedClipsRepo.
    meta = await _resolve_clip_meta(
        db,
        clip_id=item.catdv_clip_id,
        archive=archive,
        uploaded_clips_repo=uploaded_clips_repo,
    )
    clip_snapshot: dict[str, Any] = meta.clip_snapshot
    duration_secs = meta.duration_secs
    media_kind = meta.media_kind

    # Effective media resolution: per-version override > model default >
    # 'medium'. Invalid stored values are ignored (never reach the SDK map).
    # Resolved BEFORE the estimate so the estimate reads same-resolution
    # history and the resolution feedback loop is not a partial no-op.
    from backend.app.services.resolution import (
        resolution_valid_for_kind,
        resolve_media_resolution,
    )

    if force_resolution is not None:
        media_resolution = force_resolution
    else:
        _mc = await model_config_repo.get(db, version.model)
        _model_default = _mc.default_media_resolution if _mc and not _mc.removed else None
        media_resolution = resolve_media_resolution(version.media_resolution, _model_default)
    if not resolution_valid_for_kind(media_resolution, media_kind):
        # Vertex rejects HIGH for non-image media; medium is the safe ceiling.
        # Covers both branches above — even a stray calibration force_resolution
        # of 'high' on a non-image clip is sanitized here.
        media_resolution = "medium"

    # Pre-call estimate (spec §6; stamped onto the telemetry row so
    # est-vs-actual is one query). Blind to the outcome by construction.
    est: run_estimator.RunEstimate | None = None
    try:
        est = await run_estimator.estimate_clips(
            db,
            run_telemetry_repo,
            [
                run_estimator.ClipEstimateInput(
                    clip_id=item.catdv_clip_id,
                    media_kind=media_kind,
                    duration_secs=duration_secs or None,
                )
            ],
            prompt_body=version.body,
            schema=version.output_schema,
            model=version.model,
            media_resolution=media_resolution,
        )
    except Exception:  # noqa: BLE001 — estimation must never block a run
        log.exception("pre-run estimate failed for clip %s", item.catdv_clip_id)

    await jobs_repo.update_item_status(db, item.id, "prompting")
    await event_bus.publish(topic, {"item_id": item.id, "status": "prompting"})
    rendered_body = _render_prompt(version.body, duration_secs=duration_secs)
    capture = CaptureMeta(
        media_kind=media_kind,
        media_duration_secs=duration_secs or None,
        media_fps=meta.media_fps,
        media_bytes=meta.media_bytes,
        media_ext=meta.media_ext,
        clip_name=meta.clip_name,
        prompt_chars_rendered=len(rendered_body),
    )
    t0 = time.monotonic()
    # The Vertex AI client is synchronous and each call takes seconds; run it
    # off the event loop so concurrent jobs and ordinary page requests stay
    # responsive while Gemini works.
    result = await asyncio.to_thread(
        gemini.annotate,
        file_ref=file_ref,
        prompt=rendered_body,
        schema=version.output_schema,
        model=version.model,
        media_resolution=media_resolution,
    )
    elapsed_s = time.monotonic() - t0

    structured: dict[str, Any] | None
    try:
        structured = json.loads(result["text"]) if result.get("text") else None
    except json.JSONDecodeError:
        structured = None

    ai_store_kind = getattr(ai_store, "id", None)
    if record_only:
        # Calibration path: record the telemetry row + mark the item done, but
        # write NO studio-runs / review-items / annotations. Used to measure
        # cost/quality at a forced resolution without mutating review state.
        await jobs_repo.update_item_status(db, item.id, "review_ready")
        await event_bus.publish(topic, {"item_id": item.id, "status": "review_ready"})
        await _record_telemetry(
            db,
            run_telemetry_repo,
            telemetry_ctx,
            kind="studio",
            item=item,
            version=version,
            status="ok",
            result=result,
            duration_s=elapsed_s,
            capture=capture,
            est=est,
            ai_store_kind=ai_store_kind,
            media_resolution_setting=media_resolution,
        )
    elif kind == "studio":
        await _finalize_studio(
            db,
            item,
            version,
            structured,
            result,
            elapsed_s,
            duration_secs,
            studio_runs_repo,
            review_items_repo,
            jobs_repo,
            event_bus,
            topic,
            run_telemetry_repo=run_telemetry_repo,
            telemetry_ctx=telemetry_ctx,
            capture=capture,
            est=est,
            ai_store_kind=ai_store_kind,
            media_resolution_setting=media_resolution,
        )
    else:
        await _finalize_annotation(
            db,
            item,
            version,
            structured,
            result,
            rendered_body,
            clip_snapshot,
            duration_secs,
            annotations_repo,
            review_items_repo,
            jobs_repo,
            event_bus,
            topic,
            run_telemetry_repo=run_telemetry_repo,
            telemetry_ctx=telemetry_ctx,
            capture=capture,
            est=est,
            ai_store_kind=ai_store_kind,
            elapsed_s=elapsed_s,
            media_resolution_setting=media_resolution,
        )


async def _finalize_studio(
    db,
    item,
    version,
    structured,
    result,
    elapsed_s,
    duration_secs,
    studio_runs_repo: StudioRunsRepo,
    review_items_repo,
    jobs_repo,
    event_bus,
    topic,
    *,
    run_telemetry_repo: RunTelemetryRepo,
    telemetry_ctx: TelemetryCtx,
    capture: CaptureMeta,
    est=None,
    ai_store_kind: str | None = None,
    media_resolution_setting: str | None = None,
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

    usage = extract_usage(result.get("raw") or {})
    cost_usd, _ = compute_cost(usage, version.model)

    if structured is None:
        await studio_runs_repo.complete_error(db, run_id, error="model returned non-JSON or empty")
        await jobs_repo.update_item_status(db, item.id, "error", error="non-JSON output")
        await event_bus.publish(
            topic, {"item_id": item.id, "status": "error", "error": "non-JSON output"}
        )
        await _record_telemetry(
            db,
            run_telemetry_repo,
            telemetry_ctx,
            kind="studio",
            item=item,
            version=version,
            status="error",
            error_class="NonJsonOutput",
            result=result,
            duration_s=elapsed_s,
            capture=capture,
            est=est,
            ai_store_kind=ai_store_kind,
            media_resolution_setting=media_resolution_setting,
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
        db,
        run_id,
        output_json=structured,
        duration_s=elapsed_s,
        tokens_in=usage.tokens_in,
        tokens_out=usage.billable_out,
        cost_usd=cost_usd,
    )

    await jobs_repo.update_item_status(db, item.id, "review_ready")
    await event_bus.publish(
        topic, {"item_id": item.id, "status": "review_ready", "studio_run_id": run_id}
    )
    await _record_telemetry(
        db,
        run_telemetry_repo,
        telemetry_ctx,
        kind="studio",
        item=item,
        version=version,
        status="ok",
        result=result,
        duration_s=elapsed_s,
        capture=capture,
        est=est,
        ai_store_kind=ai_store_kind,
        review_item_count=len(review),
        media_resolution_setting=media_resolution_setting,
    )


async def _finalize_annotation(
    db,
    item,
    version,
    structured,
    result,
    rendered_body,
    clip_snapshot,
    duration_secs,
    annotations_repo,
    review_items_repo,
    jobs_repo,
    event_bus,
    topic,
    *,
    run_telemetry_repo: RunTelemetryRepo,
    telemetry_ctx: TelemetryCtx,
    capture: CaptureMeta,
    est=None,
    ai_store_kind: str | None = None,
    elapsed_s: float | None = None,
    media_resolution_setting: str | None = None,
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

    # Clear prior un-applied review_items for this clip so a re-run replaces
    # the working draft rather than appending to it.  Applied items are kept as
    # historical record; only the "pending" draft is replaced.  This call is
    # intentionally on the clip-annotate path only — the studio finalize path
    # uses delete_for_studio_run (keyed by studio_run_id) and must not be
    # touched here.
    await review_items_repo.clear_unapplied_for_clip(db, item.catdv_clip_id)

    review_count = 0
    if structured:
        review = expand(
            structured,
            version.target_map,
            annotation_id=annotation_id,
            catdv_clip_id=item.catdv_clip_id,
            clip_duration_secs=duration_secs or None,
        )
        review_count = len(review)
        if review:
            await review_items_repo.bulk_insert(db, review)

    await jobs_repo.update_item_status(db, item.id, "review_ready")
    await event_bus.publish(
        topic, {"item_id": item.id, "status": "review_ready", "annotation_id": annotation_id}
    )
    await _record_telemetry(
        db,
        run_telemetry_repo,
        telemetry_ctx,
        kind="annotation",
        item=item,
        version=version,
        status="ok",
        result=result,
        duration_s=elapsed_s,
        capture=capture,
        est=est,
        ai_store_kind=ai_store_kind,
        review_item_count=review_count,
        media_resolution_setting=media_resolution_setting,
    )
