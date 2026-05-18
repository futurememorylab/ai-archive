import pytest

from backend.app.services.gemini import (
    GeminiQuotaError,
    annotate_with_retry,
)


class FlakySvc:
    def __init__(self, calls_before_success: int) -> None:
        self._left = calls_before_success
        self.calls = 0

    def annotate(self, **kwargs):
        self.calls += 1
        if self._left > 0:
            self._left -= 1
            raise GeminiQuotaError("quota")
        return {"text": "ok", "raw": {}}


@pytest.mark.asyncio
async def test_retries_quota_then_succeeds():
    svc = FlakySvc(calls_before_success=2)
    result = await annotate_with_retry(
        svc, gcs_uri="gs://b/1.mov", mime="video/quicktime",
        prompt="p", schema={}, model="m",
        max_attempts=4, base_delay_secs=0.01,
    )
    assert result["text"] == "ok"
    assert svc.calls == 3


@pytest.mark.asyncio
async def test_gives_up_after_max_attempts():
    svc = FlakySvc(calls_before_success=10)
    with pytest.raises(GeminiQuotaError):
        await annotate_with_retry(
            svc, gcs_uri="gs://b/1.mov", mime="video/quicktime",
            prompt="p", schema={}, model="m",
            max_attempts=3, base_delay_secs=0.01,
        )
