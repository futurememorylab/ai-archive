"""GeminiService — orchestrates Vertex Gemini structured-output calls
for the annotator. Classifies provider errors into quota / safety /
permission variants for the caller's retry policy."""

import asyncio
import re
from collections.abc import AsyncIterator
from typing import Any, NotRequired, TypedDict

from google import genai  # type: ignore[import-not-found]
from google.genai import types  # type: ignore[import-not-found]

# Our 'low'|'medium'|'high' → the google-genai MediaResolution enum string.
_SDK_MEDIA_RESOLUTION = {
    "low": "MEDIA_RESOLUTION_LOW",
    "medium": "MEDIA_RESOLUTION_MEDIUM",
    "high": "MEDIA_RESOLUTION_HIGH",
}


class ChatTurn(TypedDict):
    role: str
    text: str
    image_jpeg: NotRequired[bytes | None]


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

    async def chat(
        self,
        *,
        model: str,
        system_instruction: str,
        turns: list[ChatTurn],
        google_search: bool = True,
    ) -> dict[str, Any]:
        """One clip-chat answer: the conversation so far in, the reply text out.

        Each turn is {"role": "user"|"model", "text": str, "image_jpeg":
        bytes | None}; only the turns that carry an image send one."""
        config = _chat_config(model, system_instruction, google_search, include_thoughts=False)
        try:
            response = await self._client.aio.models.generate_content(
                model=model, contents=_chat_contents(turns), config=config
            )
        except Exception as exc:  # noqa: BLE001
            raise _classify(exc) from exc
        return {"text": (response.text or "").strip(), "raw": response.model_dump()}

    async def chat_stream(
        self,
        *,
        model: str,
        system_instruction: str,
        turns: list[ChatTurn],
        google_search: bool = True,
    ) -> AsyncIterator[dict[str, Any]]:
        """`chat`, streamed: yields {"type": "thinking", "text": headline} while
        Gemini thinks, {"type": "text", "text": piece} as the answer is
        written, then one {"type": "done", "text", "raw", "searches",
        "sources"}. Searches and sources come only at the end: Vertex sends
        no signal while it searches."""
        config = _chat_config(model, system_instruction, google_search, include_thoughts=True)
        answer: list[str] = []
        last: Any = None
        try:
            stream = await self._client.aio.models.generate_content_stream(
                model=model, contents=_chat_contents(turns), config=config
            )
            async for chunk in stream:
                last = chunk
                for part in _chunk_parts(chunk):
                    if not part.text:
                        continue
                    if part.thought:
                        yield {"type": "thinking", "text": thought_headline(part.text)}
                    else:
                        answer.append(part.text)
                        yield {"type": "text", "text": part.text}
        except Exception as exc:  # noqa: BLE001
            raise _classify(exc) from exc
        searches, sources = _grounding(last)
        yield {
            "type": "done",
            "text": "".join(answer).strip(),
            "raw": last.model_dump() if last is not None else {},
            "searches": searches,
            "sources": sources,
        }


def _chat_contents(turns: list[ChatTurn]) -> list[types.ContentUnion]:
    contents: list[types.ContentUnion] = []
    for t in turns:
        parts = [types.Part.from_text(text=t["text"])]
        image = t.get("image_jpeg")
        if image:
            parts.append(types.Part.from_bytes(data=image, mime_type="image/jpeg"))
        contents.append(types.Content(role=t["role"], parts=parts))
    return contents


def _chat_config(
    model: str, system_instruction: str, google_search: bool, *, include_thoughts: bool
) -> types.GenerateContentConfig:
    # A chat reply should come in seconds; the default thinking is slower.
    # thinking_level exists only on Gemini 3 and later; older models 400 on it.
    level = types.ThinkingLevel.LOW if _gemini_generation(model) >= 3 else None
    return types.GenerateContentConfig(
        system_instruction=system_instruction,
        tools=[types.Tool(google_search=types.GoogleSearch())] if google_search else None,
        thinking_config=types.ThinkingConfig(
            thinking_level=level, include_thoughts=include_thoughts
        ),
    )


def _gemini_generation(model: str) -> int:
    """3 for "gemini-3.8-flash", 2 for "gemini-2.5-pro"; 0 when unknown."""
    match = re.match(r"gemini-(\d+)", model)
    return int(match.group(1)) if match else 0


def _chunk_parts(chunk: Any) -> list[Any]:
    candidates = getattr(chunk, "candidates", None) or []
    content = getattr(candidates[0], "content", None) if candidates else None
    return list(getattr(content, "parts", None) or [])


def thought_headline(text: str) -> str:
    """A thought summary opens with a bold headline ("**Recalling the
    studio's history**"); fall back to its first line."""
    match = re.search(r"\*\*(.+?)\*\*", text)
    if match:
        return match.group(1).strip()[:80]
    lines = text.strip().splitlines()
    return lines[0].strip()[:80] if lines else ""


def _grounding(chunk: Any) -> tuple[list[str], list[dict[str, str]]]:
    """The web searches Gemini ran and the pages it used (http(s) links only)."""
    candidates = getattr(chunk, "candidates", None) or []
    meta = getattr(candidates[0], "grounding_metadata", None) if candidates else None
    if meta is None:
        return [], []
    searches = [q for q in (meta.web_search_queries or []) if q]
    sources: list[dict[str, str]] = []
    for g in meta.grounding_chunks or []:
        web = g.web
        if web and web.uri and web.uri.startswith(("https://", "http://")):
            sources.append({"title": web.title or web.domain or web.uri, "uri": web.uri})
    return searches, sources


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
