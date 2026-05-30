"""is_provider_not_found narrows provider exceptions to a documented
'this clip is gone' signal. Used by CacheInspector.list_orphans(deep=True)
and WorkspaceManager.prepare so transient errors don't get treated as
permanent absence (which would orphan / fail clips on a VPN flap)."""

import httpx
import pytest

from backend.app.archive.errors import (
    AuthError,
    FatalProviderError,
    NotFoundError,
    ProviderError,
    RetryableError,
    is_provider_not_found,
)


def test_not_found_error_is_recognised():
    assert is_provider_not_found(NotFoundError("clip 42 not found")) is True


def test_httpx_404_is_recognised():
    request = httpx.Request("GET", "http://example/x")
    response = httpx.Response(404, request=request)
    exc = httpx.HTTPStatusError("404", request=request, response=response)
    assert is_provider_not_found(exc) is True


def test_httpx_500_is_not_recognised():
    request = httpx.Request("GET", "http://example/x")
    response = httpx.Response(500, request=request)
    exc = httpx.HTTPStatusError("500", request=request, response=response)
    assert is_provider_not_found(exc) is False


def test_retryable_error_is_not_recognised():
    assert is_provider_not_found(RetryableError("flaky")) is False


def test_auth_error_is_not_recognised():
    assert is_provider_not_found(AuthError("bad creds")) is False


def test_fatal_provider_error_is_not_recognised():
    """FatalProviderError on its own is not a NotFound signal — it covers
    many failures including connect errors. Only the NotFoundError subclass
    is treated as documented absence."""
    assert is_provider_not_found(FatalProviderError("connection refused")) is False


def test_arbitrary_exception_is_not_recognised():
    assert is_provider_not_found(RuntimeError("anything")) is False


def test_not_found_error_inherits_from_provider_error():
    assert issubclass(NotFoundError, ProviderError)
