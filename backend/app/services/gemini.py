"""GeminiService — orchestrates Vertex Gemini structured-output calls
for the annotator. Classifies provider errors into quota / safety /
permission variants for the caller's retry policy."""

import asyncio
from typing import Any

from google import genai  # type: ignore[import-not-found]

# Our 'low'|'medium'|'high' → the google-genai MediaResolution enum string.
_SDK_MEDIA_RESOLUTION = {
    "low": "MEDIA_RESOLUTION_LOW",
    "medium": "MEDIA_RESOLUTION_MEDIUM",
    "high": "MEDIA_RESOLUTION_HIGH",
}


class GeminiError(RuntimeError):
    pass


class GeminiQuotaError(GeminiError):
    """Rate / quota exceeded; retryable with backoff."""


class GeminiSafetyError(GeminiError):
    """Response blocked by safety policy; do not retry."""


class GeminiPermissionError(GeminiError):
    """Service account lacks required IAM; operator must fix."""


def _classify(exc: Exception) -> Exception:
    msg = str(exc).lower()
    if "quota" in msg or "resource exhausted" in msg or "rate" in msg:
        return GeminiQuotaError(str(exc))
    if "safety" in msg or "content policy" in msg or "blocked" in msg:
        return GeminiSafetyError(str(exc))
    if "permission" in msg or "access denied" in msg or "forbidden" in msg:
        return GeminiPermissionError(str(exc))
    return GeminiError(str(exc))


class GeminiService:
    def __init__(self, project: str, location: str) -> None:
        self._client = genai.Client(vertexai=True, project=project, location=location)

    def annotate(
        self,
        *,
        file_ref: dict[str, Any],
        prompt: str,
        schema: dict[str, Any],
        model: str,
        media_resolution: str | None = None,
    ) -> dict[str, Any]:
        config: dict[str, Any] = {
            "response_mime_type": "application/json",
            "response_schema": schema,
        }
        if media_resolution is not None:
            config["media_resolution"] = _SDK_MEDIA_RESOLUTION[media_resolution]
        try:
            response = self._client.models.generate_content(
                model=model,
                contents=[
                    {"text": prompt},
                    file_ref,
                ],
                config=config,
            )
        except Exception as exc:  # noqa: BLE001
            raise _classify(exc) from exc

        text = getattr(response, "text", "")
        raw = response.model_dump() if hasattr(response, "model_dump") else {}
        return {"text": text, "raw": raw}


async def annotate_with_retry(
    service: "GeminiService",
    *,
    file_ref: dict[str, Any],
    prompt: str,
    schema: dict[str, Any],
    model: str,
    max_attempts: int = 5,
    base_delay_secs: float = 1.0,
) -> dict[str, Any]:
    """Call service.annotate retrying only GeminiQuotaError with exponential backoff."""
    delay = base_delay_secs
    for attempt in range(1, max_attempts + 1):
        try:
            return service.annotate(
                file_ref=file_ref,
                prompt=prompt,
                schema=schema,
                model=model,
            )
        except GeminiQuotaError:
            if attempt >= max_attempts:
                raise
            await asyncio.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")
