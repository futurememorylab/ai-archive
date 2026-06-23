from backend.app.services.resolution import resolve_media_resolution


def test_override_wins():
    assert resolve_media_resolution("high", "low") == "high"


def test_model_default_when_no_override():
    assert resolve_media_resolution(None, "low") == "low"


def test_medium_when_neither():
    assert resolve_media_resolution(None, None) == "medium"


def test_invalid_override_falls_through_to_model_default():
    assert resolve_media_resolution("ultra", "low") == "low"


def test_invalid_values_fall_through_to_medium():
    assert resolve_media_resolution("ultra", "garbage") == "medium"
