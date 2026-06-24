"""resolution_valid_for_kind: HIGH only for still images; LOW/MEDIUM for all.

Mirrors the Vertex 400 INVALID_ARGUMENT 'HIGH media resolution only for
single images' — a calibration high-resolution job must never include a
video/audio clip.
"""

import pytest

from backend.app.services.resolution import resolution_valid_for_kind

_ALL_KINDS = ("image", "audio", "video", "video+audio")


def test_high_valid_only_for_image():
    assert resolution_valid_for_kind("high", "image") is True


@pytest.mark.parametrize("kind", ["audio", "video", "video+audio"])
def test_high_invalid_for_time_based_media(kind):
    assert resolution_valid_for_kind("high", kind) is False


@pytest.mark.parametrize("resolution", ["low", "medium"])
@pytest.mark.parametrize("kind", _ALL_KINDS)
def test_low_and_medium_valid_for_every_kind(resolution, kind):
    assert resolution_valid_for_kind(resolution, kind) is True
