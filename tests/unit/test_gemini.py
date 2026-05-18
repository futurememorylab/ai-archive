import pytest

from backend.app.services.gemini import (
    GeminiService,
    GeminiQuotaError,
    GeminiSafetyError,
    GeminiPermissionError,
)
from tests.fakes.fake_gemini import FakeGenAIClient, FakeModels, FakeResponse


def _service_with_fake_client() -> tuple[GeminiService, FakeGenAIClient]:
    fake = FakeGenAIClient(vertexai=True, project="p", location="europe-west3")
    svc = GeminiService.__new__(GeminiService)
    svc._client = fake
    return svc, fake


def test_annotate_returns_text_and_raw():
    svc, fake = _service_with_fake_client()
    fake.models.canned = FakeResponse(text='{"a": 1}', raw={"candidates": [{"text": '{"a":1}'}]})
    result = svc.annotate(
        gcs_uri="gs://b/clips/1.mov",
        mime="video/quicktime",
        prompt="describe",
        schema={"type": "object"},
        model="gemini-2.5-pro",
    )
    assert result["text"] == '{"a": 1}'
    assert result["raw"]["candidates"][0]["text"] == '{"a":1}'


def test_quota_error_is_classified():
    svc, fake = _service_with_fake_client()
    fake.models.error = RuntimeError("Resource exhausted: quota exceeded")
    with pytest.raises(GeminiQuotaError):
        svc.annotate(gcs_uri="gs://b/x.mov", mime="video/quicktime",
                     prompt="p", schema={}, model="m")


def test_safety_error_is_classified():
    svc, fake = _service_with_fake_client()
    fake.models.error = RuntimeError("SAFETY: content policy violation")
    with pytest.raises(GeminiSafetyError):
        svc.annotate(gcs_uri="gs://b/x.mov", mime="video/quicktime",
                     prompt="p", schema={}, model="m")


def test_permission_error_is_classified():
    svc, fake = _service_with_fake_client()
    fake.models.error = RuntimeError("permission denied on resource")
    with pytest.raises(GeminiPermissionError):
        svc.annotate(gcs_uri="gs://b/x.mov", mime="video/quicktime",
                     prompt="p", schema={}, model="m")
