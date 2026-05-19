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
