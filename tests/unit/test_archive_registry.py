import pytest

from backend.app.archive.providers.catdv.adapter import CatdvArchiveAdapter
from backend.app.archive.registry import build_archive_provider


class DummyClient:
    pass


def test_build_returns_catdv_adapter_when_settings_says_catdv():
    class S:
        archive_provider = "catdv"

    provider = build_archive_provider(S(), catdv_client=DummyClient())
    assert isinstance(provider, CatdvArchiveAdapter)


def test_build_raises_on_unknown_provider():
    class S:
        archive_provider = "wat"

    with pytest.raises(ValueError, match="unknown"):
        build_archive_provider(S(), catdv_client=DummyClient())


def test_build_raises_when_catdv_client_missing_for_catdv():
    class S:
        archive_provider = "catdv"

    with pytest.raises(ValueError, match="catdv_client"):
        build_archive_provider(S(), catdv_client=None)
