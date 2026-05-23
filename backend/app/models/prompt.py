"""Domain models for the prompts management feature.

A Prompt is the long-lived identity (name + description). A PromptVersion
is a snapshot of editable content (body + target_map + output_schema + model)
plus a state (draft / production / archived) that gates mutability.
"""

from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, RootModel, model_validator

PromptVersionState = Literal["draft", "production", "archived"]
PROMPT_VERSION_STATES: tuple[str, ...] = get_args(PromptVersionState)


class TargetEntry(BaseModel):
    kind: Literal["markers", "field", "note"]
    identifier: str | None = None
    target: str | None = None
    mode: Literal["append", "replace"] = "append"

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _check_required(self) -> "TargetEntry":
        if self.kind == "field" and not self.identifier:
            raise ValueError("kind=field requires 'identifier'")
        if self.kind == "note" and not self.target:
            raise ValueError("kind=note requires 'target'")
        return self


class TargetMap(RootModel[dict[str, TargetEntry]]):
    @property
    def fields(self) -> dict[str, TargetEntry]:
        return self.root


class Prompt(BaseModel):
    id: int | None = None
    name: str
    description: str | None = None
    archived: bool = False
    created_at: str
    updated_at: str

    model_config = ConfigDict(extra="allow")


class PromptVersion(BaseModel):
    id: int | None = None
    prompt_id: int
    version_num: int
    state: PromptVersionState
    body: str
    target_map: TargetMap
    output_schema: dict[str, Any]
    model: str
    created_at: str
    updated_at: str

    model_config = ConfigDict(extra="allow")
