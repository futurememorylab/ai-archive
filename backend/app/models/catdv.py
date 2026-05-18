from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TimecodeQuad(BaseModel):
    frm: int
    fmt: float
    secs: float
    txt: str

    model_config = ConfigDict(extra="allow")


class Marker(BaseModel):
    name: str
    category: str | None = None
    in_: TimecodeQuad = Field(alias="in")
    out: TimecodeQuad | None = None
    description: str | None = None
    color: str | None = None

    model_config = ConfigDict(populate_by_name=True, extra="allow")


class Clip(BaseModel):
    id: int = Field(alias="ID")
    name: str
    notes: str | None = None
    big_notes: str | None = Field(default=None, alias="bigNotes")
    format: str | None = None
    fps: float | None = None
    in_: TimecodeQuad | None = Field(default=None, alias="in")
    out: TimecodeQuad | None = None
    duration: TimecodeQuad | None = None
    markers: list[Marker] = []
    thumbnail_ids: list[int] = Field(default_factory=list, alias="thumbnailIDs")
    poster_id: int | None = Field(default=None, alias="posterID")
    media: dict[str, Any] = {}
    import_source: dict[str, Any] = Field(default_factory=dict, alias="importSource")
    history: list[Any] = []
    fields: dict[str, Any] = {}

    model_config = ConfigDict(populate_by_name=True, extra="allow")


class Envelope(BaseModel):
    status: Literal["OK", "AUTH", "ERROR"]
    error_message: str | None = Field(default=None, alias="errorMessage")
    data: Any = None

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    @property
    def is_ok(self) -> bool:
        return self.status == "OK"

    @property
    def requires_reauth(self) -> bool:
        return self.status == "AUTH"
