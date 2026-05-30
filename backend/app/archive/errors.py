"""Exception hierarchy raised by ArchiveProvider adapters (auth, retry, conflict, fatal)."""

import httpx


class ProviderError(Exception):
    """Base for any error raised by an ArchiveProvider adapter."""


class AuthError(ProviderError):
    """Credentials rejected or session expired and cannot be re-established."""


class RetryableError(ProviderError):
    """Transient failure; caller may retry with backoff."""


class ConflictError(ProviderError):
    """Optimistic-concurrency conflict against upstream state."""


class FatalProviderError(ProviderError):
    """Non-retryable failure that requires operator attention."""


class NotFoundError(ProviderError):
    """The named clip is documented as absent by the provider (e.g. CatDV 404).

    Distinct from FatalProviderError, which covers any non-retryable failure
    including transport-side ones. Only NotFoundError is safe evidence that
    a clip should be treated as 'gone' (orphaned, evictable, etc.).
    """


def is_provider_not_found(exc: BaseException) -> bool:
    """True iff `exc` is documented evidence that a clip is absent upstream.

    Recognises NotFoundError and httpx.HTTPStatusError with status 404.
    Returns False for transport failures, auth errors, retryable errors,
    and anything else — callers MUST treat False as 'transient, try later',
    never as 'gone'.
    """
    if isinstance(exc, NotFoundError):
        return True
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 404:
        return True
    return False
