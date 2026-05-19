import pytest

from backend.app.archive.errors import (
    AuthError,
    ConflictError,
    FatalProviderError,
    ProviderError,
    RetryableError,
)
from backend.app.archive.provider import ArchiveProvider, ProviderCapabilities


def test_error_hierarchy():
    assert issubclass(AuthError, ProviderError)
    assert issubclass(RetryableError, ProviderError)
    assert issubclass(ConflictError, ProviderError)
    assert issubclass(FatalProviderError, ProviderError)


def test_capabilities_is_a_frozen_dataclass():
    caps = ProviderCapabilities(
        supports_markers=True,
        supports_notes=frozenset({"notes", "bigNotes"}),
        supports_field_create=False,
        supports_etag=False,
        media_is_local=False,
        write_atomicity="per-clip",
    )
    assert "notes" in caps.supports_notes
    with pytest.raises(Exception):
        caps.supports_markers = False  # type: ignore[misc]


def test_archive_provider_is_a_protocol():
    expected = {
        "id",
        "capabilities",
        "list_clips",
        "get_clip",
        "apply_changes",
    }
    assert expected.issubset(set(dir(ArchiveProvider)))
