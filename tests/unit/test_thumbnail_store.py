from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.app.services.thumbnail_store import GcsThumbnailStore


@pytest.mark.asyncio
async def test_get_delegates_and_returns_true(tmp_path: Path):
    gcs = MagicMock()
    gcs.download_thumb.return_value = True
    store = GcsThumbnailStore(gcs)
    assert await store.get(7, tmp_path / "7.jpg") is True
    gcs.download_thumb.assert_called_once_with(7, tmp_path / "7.jpg")


@pytest.mark.asyncio
async def test_get_returns_false_on_exception(tmp_path: Path):
    gcs = MagicMock()
    gcs.download_thumb.side_effect = RuntimeError("gcs down")
    store = GcsThumbnailStore(gcs)
    assert await store.get(7, tmp_path / "7.jpg") is False


@pytest.mark.asyncio
async def test_put_delegates(tmp_path: Path):
    gcs = MagicMock()
    store = GcsThumbnailStore(gcs)
    await store.put(7, tmp_path / "7.jpg")
    gcs.upload_thumb.assert_called_once_with(7, tmp_path / "7.jpg")


@pytest.mark.asyncio
async def test_put_swallows_exception(tmp_path: Path):
    gcs = MagicMock()
    gcs.upload_thumb.side_effect = RuntimeError("gcs down")
    store = GcsThumbnailStore(gcs)
    await store.put(7, tmp_path / "7.jpg")  # must NOT raise


@pytest.mark.asyncio
async def test_get_propagates_false_from_gcs(tmp_path: Path):
    gcs = MagicMock()
    gcs.download_thumb.return_value = False  # blob absent / empty body
    store = GcsThumbnailStore(gcs)
    assert await store.get(7, tmp_path / "7.jpg") is False
