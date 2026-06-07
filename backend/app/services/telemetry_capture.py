"""Pure functions that turn a finished Gemini response into telemetry
facts: token usage (camelCase or snake_case ``usageMetadata``),
finish reason, and the prompt/schema identity hashes.

Hashes are computed over the prompt TEMPLATE (``version.body``), never
the rendered prompt — ``_render_prompt`` injects per-clip duration
text, so rendered hashes would never collide across clips and the
cross-install dedup key would be useless.
"""

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any


@dataclass(frozen=True)
class TokenUsage:
    tokens_in: int = 0
    tokens_in_text: int = 0
    tokens_in_video: int = 0
    tokens_in_audio: int = 0
    tokens_in_image: int = 0
    tokens_cached: int = 0
    tokens_out: int = 0  # candidatesTokenCount, raw
    tokens_thinking: int = 0  # thoughtsTokenCount — billed as output

    @property
    def billable_out(self) -> int:
        return self.tokens_out + self.tokens_thinking


def _get(d: dict, camel: str, snake: str) -> Any:
    v = d.get(camel)
    return v if v is not None else d.get(snake)


def _int(v: Any) -> int:
    return int(v or 0)


def extract_usage(raw: dict[str, Any]) -> TokenUsage:
    usage = _get(raw or {}, "usageMetadata", "usage_metadata") or {}
    by_modality: dict[str, int] = {}
    details = _get(usage, "promptTokensDetails", "prompt_tokens_details") or []
    for entry in details:
        modality = str(entry.get("modality") or "").upper()
        count = _int(_get(entry, "tokenCount", "token_count"))
        by_modality[modality] = by_modality.get(modality, 0) + count
    return TokenUsage(
        tokens_in=_int(_get(usage, "promptTokenCount", "prompt_token_count")),
        tokens_in_text=by_modality.get("TEXT", 0),
        tokens_in_video=by_modality.get("VIDEO", 0),
        tokens_in_audio=by_modality.get("AUDIO", 0),
        tokens_in_image=by_modality.get("IMAGE", 0),
        tokens_cached=_int(_get(usage, "cachedContentTokenCount", "cached_content_token_count")),
        tokens_out=_int(_get(usage, "candidatesTokenCount", "candidates_token_count")),
        tokens_thinking=_int(_get(usage, "thoughtsTokenCount", "thoughts_token_count")),
    )


def extract_finish_reason(raw: dict[str, Any]) -> str | None:
    candidates = (raw or {}).get("candidates") or []
    if not candidates:
        return None
    reason = _get(candidates[0] or {}, "finishReason", "finish_reason")
    return str(reason) if reason else None


@lru_cache(maxsize=128)
def prompt_hash(template_body: str) -> str:
    # Cached: the annotator hashes the same template once per processed
    # clip; a job's template is constant, so this is pure recomputation.
    return hashlib.sha256(template_body.encode("utf-8")).hexdigest()


def schema_hash(schema: dict[str, Any]) -> str:
    canonical = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
