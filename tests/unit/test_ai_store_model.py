from datetime import UTC, datetime

import pytest

from backend.app.archive.ai_store_model import (
    AIStoreCapabilities,
    StoreHealth,
    UploadedRef,
)


def test_uploaded_ref_holds_handle_and_metadata():
    ref = UploadedRef(
        handle="gs://bucket/clips/1.mov",
        mime_type="video/quicktime",
        size_bytes=12345,
        sha256="deadbeef",
        uploaded_at=datetime.now(UTC),
        expires_at=None,
    )
    assert ref.handle == "gs://bucket/clips/1.mov"
    assert ref.expires_at is None


def test_uploaded_ref_is_frozen():
    ref = UploadedRef(
        handle="gs://b/x.mov",
        mime_type="video/quicktime",
        size_bytes=1,
        sha256="a",
        uploaded_at=datetime.now(UTC),
        expires_at=None,
    )
    with pytest.raises(Exception):
        ref.handle = "gs://other"  # type: ignore[misc]


def test_capabilities_is_frozen_dataclass():
    caps = AIStoreCapabilities(
        persistent=True,
        dedup_by_sha256=True,
        max_file_bytes=10_000_000_000,
    )
    assert caps.persistent is True
    with pytest.raises(Exception):
        caps.persistent = False  # type: ignore[misc]


def test_store_health_defaults():
    h = StoreHealth(ok=True)
    assert h.ok is True
    assert h.detail is None
