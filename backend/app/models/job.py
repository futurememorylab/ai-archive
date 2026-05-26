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
]


class Job(BaseModel):
    id: int | None = None
    prompt_version_id: int
    status: JobStatus = "pending"
    total_clips: int
    notes: str | None = None
    kind: str | None = None


class JobItem(BaseModel):
    id: int | None = None
    job_id: int
    catdv_clip_id: int
    status: ItemStatus = "pending"
    error_message: str | None = None
    annotation_id: int | None = None
