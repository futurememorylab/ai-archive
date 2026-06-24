"""annotate() maps media_resolution into the generate_content config."""

from backend.app.services.gemini import GeminiService, _SDK_MEDIA_RESOLUTION


class _FakeModels:
    def __init__(self):
        self.last_config = None

    def generate_content(self, *, model, contents, config):
        self.last_config = config

        class _R:
            text = "{}"

            def model_dump(self):
                return {}

        return _R()


class _FakeClient:
    def __init__(self):
        self.models = _FakeModels()


def _svc():
    svc = GeminiService.__new__(GeminiService)  # bypass genai.Client construction
    svc._client = _FakeClient()
    return svc


def test_media_resolution_added_when_set():
    svc = _svc()
    svc.annotate(file_ref={"x": 1}, prompt="p", schema={}, model="m", media_resolution="high")
    assert svc._client.models.last_config["media_resolution"] == "MEDIA_RESOLUTION_HIGH"


def test_media_resolution_absent_when_none():
    svc = _svc()
    svc.annotate(file_ref={"x": 1}, prompt="p", schema={}, model="m")
    assert "media_resolution" not in svc._client.models.last_config


def test_sdk_map_covers_all_levels():
    assert _SDK_MEDIA_RESOLUTION == {
        "low": "MEDIA_RESOLUTION_LOW",
        "medium": "MEDIA_RESOLUTION_MEDIUM",
        "high": "MEDIA_RESOLUTION_HIGH",
    }
