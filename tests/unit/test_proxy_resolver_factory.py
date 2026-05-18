from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.app.services.proxy_resolver import (
    FilesystemProxyResolver,
    RestProxyResolver,
    build_resolver,
)


def test_factory_returns_rest_resolver(tmp_path: Path):
    fake_catdv = MagicMock()
    resolver = build_resolver(
        source="rest",
        catdv_client=fake_catdv,
        cache_dir=tmp_path / "cache",
        fs_root=None,
        path_template=None,
    )
    assert isinstance(resolver, RestProxyResolver)


def test_factory_returns_filesystem_resolver(tmp_path: Path):
    resolver = build_resolver(
        source="filesystem",
        catdv_client=None,
        cache_dir=None,
        fs_root=tmp_path,
        path_template="{root}/{clip_id}.mov",
    )
    assert isinstance(resolver, FilesystemProxyResolver)


def test_factory_rejects_filesystem_without_root():
    with pytest.raises(ValueError, match="fs_root"):
        build_resolver(
            source="filesystem",
            catdv_client=None,
            cache_dir=None,
            fs_root=None,
            path_template=None,
        )
