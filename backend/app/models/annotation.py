from typing import Any, Literal

from pydantic import BaseModel


ReviewKind = Literal["markers", "marker", "field", "note"]


class Annotation(BaseModel):
    id: int | None = None
    catdv_clip_id: int
    catdv_clip_name: str
    template_id: int
    job_id: int | None = None
    model: str
    prompt_used: str
    raw_response: dict[str, Any]
    structured_output: dict[str, Any] | None
    clip_snapshot: dict[str, Any]


class ReviewItem(BaseModel):
    id: int | None = None
    annotation_id: int
    catdv_clip_id: int
    kind: Literal["marker", "note", "field"]
    target_identifier: str | None = None
    proposed_value: dict[str, Any] | list[Any] | str | int | float | bool | None
    edited_value: dict[str, Any] | list[Any] | str | int | float | bool | None = None
    decision: Literal["pending", "accepted", "rejected"] = "pending"
