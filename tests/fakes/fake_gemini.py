from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeResponse:
    text: str
    raw: dict[str, Any] = field(default_factory=dict)

    def model_dump(self) -> dict[str, Any]:
        return self.raw or {"text": self.text}


@dataclass
class FakeModels:
    canned: Any = None
    error: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    def generate_content(self, *, model: str, contents: list, config: dict) -> FakeResponse:
        self.calls.append({"model": model, "contents": contents, "config": config})
        if self.error is not None:
            raise self.error
        return self.canned


@dataclass
class FakeGenAIClient:
    vertexai: bool
    project: str
    location: str
    models: FakeModels = field(default_factory=FakeModels)
