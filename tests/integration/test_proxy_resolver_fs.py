from pathlib import Path

import pytest

from backend.app.services.proxy_resolver import FilesystemProxyResolver, ProxyNotFound


@pytest.mark.asyncio
async def test_fs_resolver_returns_existing_path(tmp_path: Path):
    root = tmp_path / "proxies"
    root.mkdir()
    (root / "12345.mov").write_bytes(b"data")

    resolver = FilesystemProxyResolver(root=root, path_template="{root}/{clip_id}.mov")
    path = await resolver.path_for_clip_id(12345)
    assert path == root / "12345.mov"
    assert path.read_bytes() == b"data"
    assert not resolver.is_managed(path)


@pytest.mark.asyncio
async def test_fs_resolver_raises_when_missing(tmp_path: Path):
    resolver = FilesystemProxyResolver(root=tmp_path, path_template="{root}/{clip_id}.mov")
    with pytest.raises(ProxyNotFound):
        await resolver.path_for_clip_id(999)


@pytest.mark.asyncio
async def test_fs_resolver_raises_when_unreadable(tmp_path: Path):
    p = tmp_path / "123.mov"
    p.write_bytes(b"x")
    p.chmod(0)
    resolver = FilesystemProxyResolver(root=tmp_path, path_template="{root}/{clip_id}.mov")
    try:
        with pytest.raises(ProxyNotFound):
            await resolver.path_for_clip_id(123)
    finally:
        p.chmod(0o644)
