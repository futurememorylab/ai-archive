"""Telemetry domain models.

``RunTelemetryRecord`` mirrors the run_telemetry table 1:1 — one row
per Gemini call. ``TelemetryCtx`` is the install-scoped constant
context (built once at boot) threaded into the annotator so the
record builder never imports settings.
"""

from pydantic import BaseModel


class TelemetryCtx(BaseModel):
    install_id: str
    app_version: str | None = None
    archive_id: str | None = None
    vertex_project: str | None = None
    vertex_location: str | None = None


class RunTelemetryRecord(BaseModel):
    event_id: str
    occurred_at: str
    install_id: str
    app_version: str | None = None
    kind: str  # 'studio' | 'annotation'

    archive_id: str | None = None
    user_ref: str | None = None
    job_id: int | None = None
    clip_id: int | None = None
    clip_name: str | None = None

    prompt_version_id: int | None = None
    prompt_hash: str | None = None
    schema_hash: str | None = None
    prompt_chars_rendered: int | None = None
    model: str

    media_kind: str | None = None
    media_duration_secs: float | None = None
    media_width: int | None = None
    media_height: int | None = None
    media_fps: float | None = None
    media_bytes: int | None = None
    media_ext: str | None = None
    media_resolution_setting: str | None = None
    preprocess: str | None = None

    vertex_project: str | None = None
    vertex_location: str | None = None
    ai_store_kind: str | None = None

    status: str  # 'ok' | 'error'
    error_class: str | None = None
    finish_reason: str | None = None
    attempt_count: int | None = None
    duration_s: float | None = None
    tokens_in: int | None = None
    tokens_in_text: int | None = None
    tokens_in_video: int | None = None
    tokens_in_audio: int | None = None
    tokens_in_image: int | None = None
    tokens_cached: int | None = None
    tokens_out: int | None = None
    tokens_thinking: int | None = None
    cost_usd: float | None = None
    pricing_version: str | None = None

    est_tokens_in: int | None = None
    est_tokens_out_p50: int | None = None
    est_tokens_out_p90: int | None = None
    est_cost_usd_p50: float | None = None
    est_cost_usd_p90: float | None = None
    est_confidence: str | None = None

    output_chars: int | None = None
    review_item_count: int | None = None

    attrs: dict | None = None
