import pytest

from backend.app.services.gemini import (
    GeminiPermissionError,
    GeminiQuotaError,
    GeminiSafetyError,
    GeminiService,
)
from tests.fakes.fake_gemini import FakeGenAIClient, FakeResponse


def _service_with_fake_client() -> tuple[GeminiService, FakeGenAIClient]:
    fake = FakeGenAIClient(vertexai=True, project="p", location="europe-west3")
    svc = GeminiService.__new__(GeminiService)
    svc._client = fake
    return svc, fake


def test_annotate_returns_text_and_raw():
    svc, fake = _service_with_fake_client()
    fake.models.canned = FakeResponse(text='{"a": 1}', raw={"candidates": [{"text": '{"a":1}'}]})
    file_ref = {"file_data": {"file_uri": "gs://b/clips/1.mov", "mime_type": "video/quicktime"}}
    result = svc.annotate(
        file_ref=file_ref,
        prompt="describe",
        schema={"type": "object"},
        model="gemini-2.5-pro",
    )
    assert result["text"] == '{"a": 1}'
    assert result["raw"]["candidates"][0]["text"] == '{"a":1}'
    assert fake.models.calls[0]["contents"][1] == file_ref


def test_quota_error_is_classified():
    svc, fake = _service_with_fake_client()
    fake.models.error = RuntimeError("Resource exhausted: quota exceeded")
    with pytest.raises(GeminiQuotaError):
        svc.annotate(
            file_ref={"file_data": {"file_uri": "gs://b/x.mov", "mime_type": "video/quicktime"}},
            prompt="p",
            schema={},
            model="m",
        )


def test_safety_error_is_classified():
    svc, fake = _service_with_fake_client()
    fake.models.error = RuntimeError("SAFETY: content policy violation")
    with pytest.raises(GeminiSafetyError):
        svc.annotate(
            file_ref={"file_data": {"file_uri": "gs://b/x.mov", "mime_type": "video/quicktime"}},
            prompt="p",
            schema={},
            model="m",
        )


def test_permission_error_is_classified():
    svc, fake = _service_with_fake_client()
    fake.models.error = RuntimeError("permission denied on resource")
    with pytest.raises(GeminiPermissionError):
        svc.annotate(
            file_ref={"file_data": {"file_uri": "gs://b/x.mov", "mime_type": "video/quicktime"}},
            prompt="p",
            schema={},
            model="m",
        )
