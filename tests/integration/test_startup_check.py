from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.app.startup import StartupCheckResult, run_checks


class FakeCatdv:
    def __init__(self, ok: bool):
        self._ok = ok

    async def get_clip(self, clip_id):
        if not self._ok:
            raise RuntimeError("connection refused")
        return {"ID": clip_id, "name": "x"}


class FakeBucket:
    def __init__(self, ok: bool):
        self._ok = ok

    def exists(self):
        return self._ok


class FakeGcs:
    def __init__(self, ok: bool):
        self._bucket = FakeBucket(ok)


@pytest.mark.asyncio
async def test_all_checks_pass():
    result = await run_checks(
        catdv=FakeCatdv(True),
        gcs=FakeGcs(True),
        proxy_resolver=MagicMock(path_for_clip_id=MagicMock()),
        catalog_id=881507,
        sample_clip_id=1,
        verify_proxy=False,
    )
    assert result.ok
    assert result.failures == []


@pytest.mark.asyncio
async def test_catdv_failure_is_reported():
    result = await run_checks(
        catdv=FakeCatdv(False),
        gcs=FakeGcs(True),
        proxy_resolver=MagicMock(),
        catalog_id=881507,
        sample_clip_id=1,
        verify_proxy=False,
    )
    assert not result.ok
    assert any("CatDV" in f for f in result.failures)


@pytest.mark.asyncio
async def test_gcs_bucket_missing_is_reported():
    result = await run_checks(
        catdv=FakeCatdv(True),
        gcs=FakeGcs(False),
        proxy_resolver=MagicMock(),
        catalog_id=881507,
        sample_clip_id=1,
        verify_proxy=False,
    )
    assert not result.ok
    assert any("GCS" in f for f in result.failures)
