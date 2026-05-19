from backend.app.archive.ai_store import AIInputStore


def test_ai_input_store_protocol_exposes_expected_names():
    expected = {
        "id",
        "capabilities",
        "ensure_uploaded",
        "status",
        "evict",
        "health",
        "reference_for_gemini",
    }
    assert expected.issubset(set(dir(AIInputStore)))


import pytest

from backend.app.archive.ai_store import AIInputStore as _AIInputStore  # noqa: F401
from backend.app.archive.ai_stores.gemini_files.adapter import (
    GeminiFilesInputStore,
)


def test_gemini_files_stub_advertises_correct_id_and_capabilities():
    stub = GeminiFilesInputStore()
    assert stub.id == "gemini-files"
    assert stub.capabilities.persistent is False
    assert stub.capabilities.dedup_by_sha256 is False
    assert stub.capabilities.max_file_bytes == 2 * 1024 * 1024 * 1024  # 2 GB


@pytest.mark.asyncio
async def test_gemini_files_stub_methods_raise_not_implemented(tmp_path):
    stub = GeminiFilesInputStore()
    local = tmp_path / "x.mov"
    local.write_bytes(b"x")
    with pytest.raises(NotImplementedError):
        await stub.ensure_uploaded(("catdv", "1"), local, "video/quicktime")
    with pytest.raises(NotImplementedError):
        await stub.status(("catdv", "1"))
    with pytest.raises(NotImplementedError):
        await stub.evict(("catdv", "1"))
    with pytest.raises(NotImplementedError):
        await stub.health()
