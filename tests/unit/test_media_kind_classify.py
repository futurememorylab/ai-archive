"""classify_media_kind: image | audio | video | video+audio.

Unknown audio presence on a video defaults to video+audio — the
conservative estimate (slightly over, never under). Existing
is_image_path behavior must not change.
"""

import pytest

from backend.app.media_kind import classify_media_kind, is_image_path


@pytest.mark.parametrize(
    ("path", "has_audio", "expected"),
    [
        ("clips/a.jpg", None, "image"),
        ("clips/a.PNG", None, "image"),
        ("clips/a.wav", None, "audio"),
        ("clips/a.MP3", None, "audio"),
        ("clips/a.mov", None, "video+audio"),   # unknown audio → conservative
        ("clips/a.mp4", True, "video+audio"),
        ("clips/a.mp4", False, "video"),
        ("clips/a.wav", False, "audio"),        # extension wins over has_audio
        (None, None, "video+audio"),            # nothing known → conservative
        ("noext", None, "video+audio"),
    ],
)
def test_classify(path, has_audio, expected):
    assert classify_media_kind(path, has_audio=has_audio) == expected


def test_is_image_path_unchanged():
    assert is_image_path("x.jpeg") is True
    assert is_image_path("x.mov") is False
    assert is_image_path(None) is False
