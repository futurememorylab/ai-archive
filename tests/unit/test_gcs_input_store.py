import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.archive.ai_store_model import (
    AIStoreCapabilities,
    StoreHealth,
    UploadedRef,
)
from backend.app.archive.ai_stores.gcs.adapter import GcsInputStore


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _make_fake_db() -> MagicMock:
    """The adapter is constructed with a callable that yields the live db
    connection per call. Tests pass a sentinel; the repo is mocked anyway."""
    return MagicMock(name="db_conn")


class FakeRepo:
    """Stands in for AIStoreFilesRepo. In-memory dict."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, int], dict] = {}

    async def get(self, db, *, store_id: str, clip_id: int):
        return self.rows.get((store_id, clip_id))

    async def upsert(self, db, *, store_id: str, clip_id: int, **kwargs):
        self.rows[(store_id, clip_id)] = {
            "store_id": store_id, "catdv_clip_id": clip_id, **kwargs,
            "uploaded_at": "now", "last_used_at": "now",
        }

    async def touch(self, db, *, store_id: str, clip_id: int):
        self.rows[(store_id, clip_id)]["last_used_at"] = "later"

    async def delete(self, db, *, store_id: str, clip_id: int):
        self.rows.pop((store_id, clip_id), None)


class FakeGcsService:
    def __init__(self, bucket_name: str = "test-bucket"):
        self.bucket_name = bucket_name
        self.uploads: list[tuple[int, Path, str]] = []
        self.deletes: list[int] = []
        self._bucket = MagicMock()
        self._bucket.exists = MagicMock(return_value=True)

    def gs_uri(self, clip_id: int) -> str:
        return f"gs://{self.bucket_name}/clips/{clip_id}.mov"

    def upload_if_absent(self, *, clip_id: int, local_path: Path, mime: str) -> str:
        self.uploads.append((clip_id, local_path, mime))
        return self.gs_uri(clip_id)

    def delete(self, *, clip_id: int) -> None:
        self.deletes.append(clip_id)


@pytest.fixture
def adapter_factory(tmp_path):
    def _factory(*, bucket: str = "test-bucket"):
        gcs = FakeGcsService(bucket_name=bucket)
        repo = FakeRepo()
        db = _make_fake_db()
        adapter = GcsInputStore(
            gcs=gcs, files_repo=repo, db_provider=lambda: db
        )
        return adapter, gcs, repo, db
    return _factory


def test_id_is_gcs_prefixed_with_bucket(adapter_factory):
    adapter, _, _, _ = adapter_factory(bucket="my-bucket")
    assert adapter.id == "gcs:my-bucket"


def test_capabilities(adapter_factory):
    adapter, _, _, _ = adapter_factory()
    assert isinstance(adapter.capabilities, AIStoreCapabilities)
    assert adapter.capabilities.persistent is True
    assert adapter.capabilities.dedup_by_sha256 is True
    assert adapter.capabilities.max_file_bytes > 0


@pytest.mark.asyncio
async def test_ensure_uploaded_first_time_uploads_and_records(adapter_factory, tmp_path):
    adapter, gcs, repo, db = adapter_factory()
    local = tmp_path / "1.mov"
    local.write_bytes(b"hello")

    ref = await adapter.ensure_uploaded(
        clip_key=("catdv", "1"), local_path=local, mime="video/quicktime"
    )

    assert isinstance(ref, UploadedRef)
    assert ref.handle == "gs://test-bucket/clips/1.mov"
    assert ref.mime_type == "video/quicktime"
    assert ref.sha256 == _sha256(local)
    assert gcs.uploads == [(1, local, "video/quicktime")]
    assert ("gcs:test-bucket", 1) in repo.rows


@pytest.mark.asyncio
async def test_ensure_uploaded_dedups_when_sha256_matches(adapter_factory, tmp_path):
    adapter, gcs, repo, db = adapter_factory()
    local = tmp_path / "1.mov"
    local.write_bytes(b"hello")
    sha = _sha256(local)

    # Pre-seed the repo as if a previous upload happened.
    repo.rows[("gcs:test-bucket", 1)] = {
        "store_id": "gcs:test-bucket",
        "catdv_clip_id": 1,
        "gcs_uri": "gs://test-bucket/clips/1.mov",
        "mime_type": "video/quicktime",
        "size_bytes": 5,
        "sha256": sha,
        "uploaded_at": "earlier",
        "last_used_at": "earlier",
        "expires_at": None,
    }

    ref = await adapter.ensure_uploaded(
        clip_key=("catdv", "1"), local_path=local, mime="video/quicktime"
    )
    assert ref.handle == "gs://test-bucket/clips/1.mov"
    assert gcs.uploads == []  # no re-upload
    # touch happened.
    assert repo.rows[("gcs:test-bucket", 1)]["last_used_at"] == "later"


@pytest.mark.asyncio
async def test_ensure_uploaded_reuploads_when_sha256_mismatch(adapter_factory, tmp_path):
    adapter, gcs, repo, db = adapter_factory()
    local = tmp_path / "1.mov"
    local.write_bytes(b"new bytes")

    repo.rows[("gcs:test-bucket", 1)] = {
        "store_id": "gcs:test-bucket",
        "catdv_clip_id": 1,
        "gcs_uri": "gs://test-bucket/clips/1.mov",
        "mime_type": "video/quicktime",
        "size_bytes": 5,
        "sha256": "STALE_SHA",
        "uploaded_at": "earlier",
        "last_used_at": "earlier",
        "expires_at": None,
    }

    ref = await adapter.ensure_uploaded(
        clip_key=("catdv", "1"), local_path=local, mime="video/quicktime"
    )
    assert ref.sha256 == _sha256(local)
    assert gcs.uploads == [(1, local, "video/quicktime")]


@pytest.mark.asyncio
async def test_status_returns_none_when_absent(adapter_factory):
    adapter, _, _, _ = adapter_factory()
    assert await adapter.status(("catdv", "1")) is None


@pytest.mark.asyncio
async def test_status_returns_uploaded_ref_when_present(adapter_factory, tmp_path):
    adapter, _, repo, _ = adapter_factory()
    repo.rows[("gcs:test-bucket", 1)] = {
        "store_id": "gcs:test-bucket",
        "catdv_clip_id": 1,
        "gcs_uri": "gs://test-bucket/clips/1.mov",
        "mime_type": "video/quicktime",
        "size_bytes": 5,
        "sha256": "abc",
        "uploaded_at": "2026-05-19T00:00:00+00:00",
        "last_used_at": "2026-05-19T00:00:00+00:00",
        "expires_at": None,
    }
    ref = await adapter.status(("catdv", "1"))
    assert ref is not None
    assert ref.handle == "gs://test-bucket/clips/1.mov"
    assert ref.sha256 == "abc"


@pytest.mark.asyncio
async def test_evict_deletes_blob_and_row(adapter_factory):
    adapter, gcs, repo, _ = adapter_factory()
    repo.rows[("gcs:test-bucket", 7)] = {
        "store_id": "gcs:test-bucket",
        "catdv_clip_id": 7,
        "gcs_uri": "gs://test-bucket/clips/7.mov",
        "mime_type": "video/quicktime",
        "size_bytes": 1,
        "sha256": "z",
        "uploaded_at": "x",
        "last_used_at": "x",
        "expires_at": None,
    }
    await adapter.evict(("catdv", "7"))
    assert gcs.deletes == [7]
    assert ("gcs:test-bucket", 7) not in repo.rows


@pytest.mark.asyncio
async def test_evict_is_noop_when_no_row(adapter_factory):
    adapter, gcs, _, _ = adapter_factory()
    # Should not raise; no row, no blob delete.
    await adapter.evict(("catdv", "404"))
    assert gcs.deletes == []


@pytest.mark.asyncio
async def test_reference_for_gemini_returns_file_data_shape(adapter_factory):
    from datetime import datetime, timezone

    adapter, _, _, _ = adapter_factory()
    ref = UploadedRef(
        handle="gs://test-bucket/clips/1.mov",
        mime_type="video/quicktime",
        size_bytes=10,
        sha256="x",
        uploaded_at=datetime.now(timezone.utc),
        expires_at=None,
    )
    out = await adapter.reference_for_gemini(ref)
    assert out == {
        "file_data": {
            "file_uri": "gs://test-bucket/clips/1.mov",
            "mime_type": "video/quicktime",
        }
    }


@pytest.mark.asyncio
async def test_health_reports_bucket_exists(adapter_factory):
    adapter, gcs, _, _ = adapter_factory()
    h = await adapter.health()
    assert isinstance(h, StoreHealth)
    assert h.ok is True


@pytest.mark.asyncio
async def test_health_reports_failure_when_bucket_missing(adapter_factory):
    adapter, gcs, _, _ = adapter_factory()
    gcs._bucket.exists = MagicMock(return_value=False)
    h = await adapter.health()
    assert h.ok is False
    assert "test-bucket" in (h.detail or "")
