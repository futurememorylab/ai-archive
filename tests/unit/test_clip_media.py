"""Unit tests for the canonical cache-layer helper (services/clip_media.py)."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.services.catdv_client import ProgressCb
from backend.app.services.clip_media import ai_input_for_clip
from backend.app.services.proxy_resolver import ProxyNotFound

KEY = ("catdv", "42")


async def _tracker(done: int, total: int) -> None:
    """Stand-in download progress callback."""


class _Progress:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.tracker = _tracker

    async def missed(self) -> ProgressCb | None:
        self.events.append("missed")
        return self.tracker

    async def uploading(self) -> None:
        self.events.append("uploading")


def _layers(*, status=None, path=Path("/cache/42.mov"), missing=False):
    ai_store = MagicMock()
    ai_store.status = AsyncMock(return_value=status)
    ai_store.ensure_uploaded = AsyncMock(return_value="uploaded-ref")
    resolver = MagicMock()
    resolver.path_for_clip_id = AsyncMock(
        side_effect=ProxyNotFound("no proxy") if missing else None, return_value=path
    )
    return ai_store, resolver


async def test_ai_store_hit_skips_proxy_and_upload():
    ai_store, resolver = _layers(status="existing-ref")
    progress = _Progress()
    ref = await ai_input_for_clip(
        KEY, 42, ai_store=ai_store, proxy_resolver=resolver, progress=progress
    )
    assert ref == "existing-ref"
    resolver.path_for_clip_id.assert_not_awaited()
    ai_store.ensure_uploaded.assert_not_awaited()
    assert progress.events == []


async def test_miss_resolves_proxy_then_uploads_with_its_mime():
    ai_store, resolver = _layers(path=Path("/cache/42.mp4"))
    progress = _Progress()
    ref = await ai_input_for_clip(
        KEY, 42, ai_store=ai_store, proxy_resolver=resolver, progress=progress
    )
    assert ref == "uploaded-ref"
    resolver.path_for_clip_id.assert_awaited_once_with(42, progress.tracker)
    ai_store.ensure_uploaded.assert_awaited_once_with(KEY, Path("/cache/42.mp4"), "video/mp4")
    assert progress.events == ["missed", "uploading"]


async def test_unknown_extension_falls_back_to_quicktime():
    ai_store, resolver = _layers(path=Path("/cache/42"))
    await ai_input_for_clip(KEY, 42, ai_store=ai_store, proxy_resolver=resolver)
    resolver.path_for_clip_id.assert_awaited_once_with(42, None)
    assert ai_store.ensure_uploaded.await_args.args[2] == "video/quicktime"


async def test_miss_in_both_layers_raises_proxy_not_found():
    ai_store, resolver = _layers(missing=True)
    progress = _Progress()
    with pytest.raises(ProxyNotFound):
        await ai_input_for_clip(
            KEY, 42, ai_store=ai_store, proxy_resolver=resolver, progress=progress
        )
    ai_store.ensure_uploaded.assert_not_awaited()
    assert progress.events == ["missed"]
