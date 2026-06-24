"""Media resolution — a fixed enum controlling how many tokens a clip's media
costs in a Gemini call (low/medium/high). Source of truth for the Literal;
the enum registry pins to it (test_media_resolution_enum)."""

from typing import Literal

MediaResolution = Literal["low", "medium", "high"]

DEFAULT_MEDIA_RESOLUTION: MediaResolution = "medium"
