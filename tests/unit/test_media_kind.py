import pytest

from backend.app.media_kind import is_image_path


@pytest.mark.parametrize(
    "path",
    [
        "/Volumes/ARECA/x/Abramcukova Anna 101.JPG",
        "photo.jpeg",
        "scan.PNG",
        "neg.tif",
        "neg.tiff",
        "anim.gif",
        "bitmap.bmp",
        "modern.webp",
        "iphone.heic",
    ],
)
def test_image_paths_are_images(path):
    assert is_image_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "/Volumes/ARECA/x/ARNOLD Bogdan Sis 101.mov",
        "clip.mp4",
        "movie.mkv",
        "broadcast.mxf",
        "noext",
        "",
        None,
    ],
)
def test_non_image_paths_are_not_images(path):
    assert is_image_path(path) is False
