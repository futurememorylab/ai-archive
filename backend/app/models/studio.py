"""Pydantic models for Prompt Studio + the shared AnnotationOutput dataclass."""
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, model_validator

SourceKind = Literal["upload", "catdv_clip"]
RunStatus = Literal["pending", "running", "completed", "failed", "cancelled"]
ItemStatus = Literal[
    "pending", "resolving", "uploading", "prompting",
    "done", "error", "unacceptable",
]


class Testbench(BaseModel):
    id: int
    name: str
    description: str | None
    archived: bool
    created_at: str
    updated_at: str


class TestbenchFolder(BaseModel):
    id: int
    testbench_id: int
    parent_id: int | None
    name: str
    sort_index: int
    created_at: str


class TestbenchItem(BaseModel):
    id: int
    folder_id: int
    source_kind: SourceKind
    upload_path: str | None
    upload_orig_name: str | None
    catdv_provider_clip_id: str | None
    display_name: str
    gold_json: str | None
    sort_index: int
    created_at: str

    @model_validator(mode="after")
    def _check_source_consistency(self) -> "TestbenchItem":
        if self.source_kind == "upload":
            if not self.upload_path or self.catdv_provider_clip_id is not None:
                raise ValueError("upload kind requires upload_path and no catdv_provider_clip_id")
        else:
            if not self.catdv_provider_clip_id or self.upload_path is not None:
                raise ValueError("catdv_clip kind requires catdv_provider_clip_id and no upload_path")
        return self


class StudioRun(BaseModel):
    id: int
    testbench_id: int
    prompt_version_id: int
    status: RunStatus
    created_at: str
    started_at: str | None
    finished_at: str | None
    notes: str | None


class StudioRunItem(BaseModel):
    id: int
    run_id: int
    testbench_item_id: int
    status: ItemStatus
    error: str | None
    unacceptable_reason: str | None
    structured_json: str | None
    raw_text: str | None
    prompt_used: str | None
    model: str | None
    latency_ms: int | None
    started_at: str | None
    finished_at: str | None


@dataclass
class AnnotationOutput:
    """Result of one Gemini per-item annotation pass — shape shared by
    `services/annotator.py::run_job` and `services/studio_runs.py::run`."""
    structured: dict[str, Any] | None
    raw_text: str
    raw: dict[str, Any]      # full gemini response dict — preserved for annotations.raw_response
    prompt_used: str
    model: str
    latency_ms: int
