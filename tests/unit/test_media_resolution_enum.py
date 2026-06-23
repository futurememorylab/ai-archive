"""media_resolution is a fixed enum; registry values pinned to the Literal."""

from typing import get_args

from backend.app.enums.registry import ENUM_REGISTRY
from backend.app.models.media import MediaResolution


def test_media_resolution_registry_matches_literal():
    spec = ENUM_REGISTRY["media_resolution"]
    assert spec.editable is False
    assert tuple(v.value for v in spec.values) == get_args(MediaResolution)


def test_media_resolution_values():
    assert get_args(MediaResolution) == ("low", "medium", "high")
