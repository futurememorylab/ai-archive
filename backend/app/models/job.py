"""Pydantic models for annotation jobs and per-clip job items — persisted
by JobsRepo."""

from typing import Literal

from pydantic import BaseModel

JobStatus = Literal["pending", "running", "completed", "failed", "cancelled"]
ItemStatus = Literal[
    "pending",
    "resolving",
    "uploading",
    "prompting",
    "annotated",
    "review_ready",
    "applied",
    "rejected",
    "error",
    "cancelled",  # item of a job interrupted by a restart (orphan recovery)
]


class Job(BaseModel):
    id: int | None = None
    prompt_version_id: int
    status: JobStatus = "pending"
    total_clips: int
    notes: str | None = None
    kind: str | None = None
    run_group: str | None = None
    # Per-job run parameters read by the JobRunner when it executes the job
    # (ADR 0125). Set on calibration sweeps; default for normal jobs.
    force_resolution: str | None = None
    record_only: bool = False


class JobItem(BaseModel):
    id: int | None = None
    job_id: int
    catdv_clip_id: int
    status: ItemStatus = "pending"
    error_message: str | None = None
    annotation_id: int | None = None
