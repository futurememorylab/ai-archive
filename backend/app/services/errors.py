"""User-facing error-string helpers.

`humanise(exc)` turns any exception into an actionable, non-empty string
suitable for showing the user (job error messages, toast text, etc.).
Required because `str(exc)` is empty or unhelpful for many SDK
exceptions (httpx.HTTPStatusError, google.api_core errors).
"""

from __future__ import annotations

import httpx

_MAX_BODY_CHARS = 400


def humanise(exc: BaseException) -> str:
    """Return an actionable, non-empty error string for the user.

    - httpx.HTTPStatusError: includes status code AND a truncated snippet
      of the response body.
    - httpx.ConnectError / TimeoutException / RequestError: a clear
      transport phrase.
    - other exceptions: str(exc) if non-empty, otherwise the class name.

    Always returns a non-empty string; HTTP error bodies truncated at
    `_MAX_BODY_CHARS`, so total length is bounded to roughly that plus
    the URL/prefix overhead (~80 chars).
    """
    if isinstance(exc, httpx.HTTPStatusError):
        body = (exc.response.text or "").strip()
        if len(body) > _MAX_BODY_CHARS:
            body = body[:_MAX_BODY_CHARS] + "…(truncated)"
        return f"HTTP {exc.response.status_code} from {exc.request.url}: {body}" if body \
            else f"HTTP {exc.response.status_code} from {exc.request.url}"
    if isinstance(exc, httpx.TimeoutException):
        return f"transport timeout: {exc}"
    if isinstance(exc, httpx.ConnectError):
        return f"connect failed: {exc}"
    if isinstance(exc, httpx.RequestError):
        return f"transport error ({type(exc).__name__}): {exc}"
    s = str(exc).strip()
    return s if s else type(exc).__name__
