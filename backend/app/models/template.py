from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, RootModel, model_validator


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


class Template(BaseModel):
    id: int | None = None
    name: str
    description: str | None = None
    prompt: str
    output_schema: dict[str, Any]
    target_map: TargetMap
    model: str
    archived: bool = False

    model_config = ConfigDict(extra="allow")
