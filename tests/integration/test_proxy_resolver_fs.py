import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.services.media_store_map import MediaStoreMap
from backend.app.services.proxy_resolver import (
    FilesystemProxyResolver,
    ProxyNotFound,
)


class _FakeArchive:
    def __init__(self, clip_by_id: dict[int, dict]):
        self._by_id = clip_by_id

    async def get_clip(self, clip_id_str: str):
        return SimpleNamespace(provider_data=self._by_id[int(clip_id_str)])


def _map_with(hires_root: Path, proxy_root: Path) -> MediaStoreMap:
    return MediaStoreMap(rules=[(str(hires_root), str(proxy_root))])


@pytest.mark.asyncio
async def test_returns_existing_proxy_path(tmp_path: Path):
    hires_root = tmp_path / "hires"
    proxy_root = tmp_path / "proxy"
    (proxy_root / "sub").mkdir(parents=True)
    proxy_file = proxy_root / "sub" / "clip.mov"
    proxy_file.write_bytes(b"x")

    archive = _FakeArchive({42: {"media": {"filePath": str(hires_root / "sub" / "clip.mov")}}})
    resolver = FilesystemProxyResolver(
        archive=archive,
        media_store_map=_map_with(hires_root, proxy_root),
    )

    assert await resolver.path_for_clip_id(42) == proxy_file
    assert resolver.is_managed(proxy_file) is False


@pytest.mark.asyncio
async def test_raises_when_clip_has_no_media_filepath(tmp_path: Path):
    archive = _FakeArchive({42: {"media": {}}})
    resolver = FilesystemProxyResolver(
        archive=archive,
        media_store_map=_map_with(tmp_path / "h", tmp_path / "p"),
    )
    with pytest.raises(ProxyNotFound, match="no media.filePath"):
        await resolver.path_for_clip_id(42)


@pytest.mark.asyncio
async def test_raises_when_hires_path_unknown_to_mediastore(tmp_path: Path):
    archive = _FakeArchive({42: {"media": {"filePath": "/some/unmapped/path.mov"}}})
    resolver = FilesystemProxyResolver(
        archive=archive,
        media_store_map=_map_with(tmp_path / "h", tmp_path / "p"),
    )
    with pytest.raises(ProxyNotFound, match="no mediastore rule"):
        await resolver.path_for_clip_id(42)


@pytest.mark.asyncio
async def test_raises_when_proxy_file_missing_on_disk(tmp_path: Path):
    hires_root = tmp_path / "hires"
    proxy_root = tmp_path / "proxy"
    proxy_root.mkdir()
    archive = _FakeArchive({42: {"media": {"filePath": str(hires_root / "sub" / "clip.mov")}}})
    resolver = FilesystemProxyResolver(
        archive=archive,
        media_store_map=_map_with(hires_root, proxy_root),
    )
    with pytest.raises(ProxyNotFound, match="not on disk"):
        await resolver.path_for_clip_id(42)


@pytest.mark.asyncio
async def test_raises_when_proxy_unreadable(tmp_path: Path):
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("chmod(0) does not restrict the root user")
    hires_root = tmp_path / "hires"
    proxy_root = tmp_path / "proxy"
    proxy_root.mkdir()
    proxy_file = proxy_root / "clip.mov"
    proxy_file.write_bytes(b"x")
    proxy_file.chmod(0)
    archive = _FakeArchive({42: {"media": {"filePath": str(hires_root / "clip.mov")}}})
    resolver = FilesystemProxyResolver(
        archive=archive,
        media_store_map=_map_with(hires_root, proxy_root),
    )
    try:
        with pytest.raises(ProxyNotFound, match="not readable"):
            await resolver.path_for_clip_id(42)
    finally:
        proxy_file.chmod(0o644)
