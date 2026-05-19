from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.app.archive.ai_store_model import StoreHealth
from backend.app.startup import StartupCheckResult, run_checks


class FakeCatdv:
    def __init__(self, ok: bool):
        self._ok = ok

    async def get_clip(self, clip_id):
        if not self._ok:
            raise RuntimeError("connection refused")
        return {"ID": clip_id, "name": "x"}


class FakeAIStore:
    def __init__(self, ok: bool, detail: str | None = None):
        self._ok = ok
        self._detail = detail

    async def health(self) -> StoreHealth:
        return StoreHealth(ok=self._ok, detail=self._detail)


@pytest.mark.asyncio
async def test_all_checks_pass():
    result = await run_checks(
        catdv=FakeCatdv(True),
        ai_store=FakeAIStore(True),
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
        ai_store=FakeAIStore(True),
        proxy_resolver=MagicMock(),
        catalog_id=881507,
        sample_clip_id=1,
        verify_proxy=False,
    )
    assert not result.ok
    assert any("CatDV" in f for f in result.failures)


@pytest.mark.asyncio
async def test_ai_store_failure_is_reported():
    result = await run_checks(
        catdv=FakeCatdv(True),
        ai_store=FakeAIStore(False, detail="bucket not found: b"),
        proxy_resolver=MagicMock(),
        catalog_id=881507,
        sample_clip_id=1,
        verify_proxy=False,
    )
    assert not result.ok
    assert any("AI input store" in f for f in result.failures)
