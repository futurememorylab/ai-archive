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
        archive=None,
        media_store_map=None,
    )
    assert isinstance(resolver, RestProxyResolver)


def test_factory_returns_filesystem_resolver():
    from backend.app.services.media_store_map import MediaStoreMap
    resolver = build_resolver(
        source="filesystem",
        catdv_client=None,
        cache_dir=None,
        archive=object(),
        media_store_map=MediaStoreMap(),
    )
    assert isinstance(resolver, FilesystemProxyResolver)


def test_factory_rejects_filesystem_without_archive_and_map():
    with pytest.raises(ValueError, match="archive provider and media_store_map"):
        build_resolver(
            source="filesystem",
            catdv_client=None,
            cache_dir=None,
            archive=None,
            media_store_map=None,
        )
