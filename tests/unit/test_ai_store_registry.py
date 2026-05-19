import pytest

from backend.app.archive.ai_stores.gcs.adapter import GcsInputStore
from backend.app.archive.ai_stores.gemini_files.adapter import (
    GeminiFilesInputStore,
)
from backend.app.archive.ai_stores.registry import build_ai_input_store


class FakeGcs:
    def __init__(self, bucket_name: str = "b"):
        self.bucket_name = bucket_name


class FakeRepo:
    pass


def _settings(name: str):
    class S:
        ai_input_store = name

    return S()


def test_build_returns_gcs_adapter_when_settings_says_gcs():
    store = build_ai_input_store(
        _settings("gcs"),
        gcs_service=FakeGcs(),
        files_repo=FakeRepo(),
        db_provider=lambda: None,
    )
    assert isinstance(store, GcsInputStore)
    assert store.id == "gcs:b"


def test_build_returns_gemini_files_stub_when_settings_says_gemini_files():
    store = build_ai_input_store(
        _settings("gemini-files"),
        gcs_service=None,
        files_repo=FakeRepo(),
        db_provider=lambda: None,
    )
    assert isinstance(store, GeminiFilesInputStore)
    assert store.id == "gemini-files"


def test_build_raises_on_unknown_store():
    with pytest.raises(ValueError, match="unknown"):
        build_ai_input_store(
            _settings("nope"),
            gcs_service=FakeGcs(),
            files_repo=FakeRepo(),
            db_provider=lambda: None,
        )


def test_build_raises_when_gcs_service_missing_for_gcs():
    with pytest.raises(ValueError, match="gcs_service"):
        build_ai_input_store(
            _settings("gcs"),
            gcs_service=None,
            files_repo=FakeRepo(),
            db_provider=lambda: None,
        )
