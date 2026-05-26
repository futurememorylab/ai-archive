"""Domain models for Prompt Studio.

A StudioFolder is a flat-named bucket of clips picked from the archive.
A StudioFolderClip is one row of the membership table.
A StudioRun is one execution of a prompt version against a clip; one row
per execution. History kept forever; UI shows the latest per
(prompt_version_id, clip_id).
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

StudioRunStatus = Literal["pending", "running", "ok", "error"]


class StudioFolder(BaseModel):
    id: int | None = None
    name: str
    created_at: str

    model_config = ConfigDict(extra="forbid")


class StudioFolderClip(BaseModel):
    folder_id: int
    clip_id: int
    added_at: str

    model_config = ConfigDict(extra="forbid")


class StudioRun(BaseModel):
    id: int | None = None
    prompt_version_id: int
    clip_id: int
    job_id: int | None = None
    status: StudioRunStatus
    output_json: dict[str, Any] | None = None
    duration_s: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    model: str | None = None
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None

    model_config = ConfigDict(extra="forbid")
