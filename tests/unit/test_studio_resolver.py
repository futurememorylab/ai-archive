from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.services.studio_runs import (
    ResolvedInput, Unacceptable, resolve_clip_input,
)


class _Item:
    def __init__(self, *, kind, upload_path=None, catdv_id=None, display_name="x"):
        self.source_kind = kind
        self.upload_path = upload_path
        self.catdv_provider_clip_id = catdv_id
        self.display_name = display_name


@pytest.mark.asyncio
async def test_upload_returns_local_path(tmp_path):
    f = tmp_path / "u.mp4"
    f.write_bytes(b"\x00")
    item = _Item(kind="upload", upload_path="u.mp4")
    out = await resolve_clip_input(
        item, mode="online",
        proxy_resolver=MagicMock(), archive=MagicMock(),
        cache_only_resolver=MagicMock(), clip_cache_repo=MagicMock(),
        ai_store=MagicMock(), db=MagicMock(),
        uploads_root=tmp_path,
    )
    assert isinstance(out, ResolvedInput)
    assert out.local_path == f
    assert out.archive_lookup_arg is None


@pytest.mark.asyncio
async def test_catdv_online_uses_archive(tmp_path):
    item = _Item(kind="catdv_clip", catdv_id="123")
    canonical = MagicMock()
    canonical.duration_secs = 30.0
    canonical.provider_data = {"id": 123, "name": "n"}
    archive = MagicMock(); archive.get_clip = AsyncMock(return_value=canonical)
    resolver = MagicMock()
    proxy = tmp_path / "123.mov"
    proxy.write_bytes(b"\x00")
    resolver.path_for_clip_id = AsyncMock(return_value=proxy)
    out = await resolve_clip_input(
        item, mode="online",
        proxy_resolver=resolver, archive=archive,
        cache_only_resolver=MagicMock(), clip_cache_repo=MagicMock(),
        ai_store=MagicMock(), db=MagicMock(), uploads_root=tmp_path,
    )
    assert isinstance(out, ResolvedInput)
    assert out.local_path == proxy
    assert out.archive_lookup_arg == "123"
    assert out.clip_snapshot["name"] == "n"


@pytest.mark.asyncio
async def test_catdv_offline_falls_back_to_cache(tmp_path):
    item = _Item(kind="catdv_clip", catdv_id="123")
    cache_only = MagicMock()
    p = tmp_path / "c.mov"
    p.write_bytes(b"\x00")
    cache_only.path_for_clip_id = AsyncMock(return_value=p)
    cached_clip = MagicMock()
    cached_clip.provider_data = {"id": 123, "name": "cached"}
    clip_cache = MagicMock()
    clip_cache.get_by_key = AsyncMock(return_value=cached_clip)
    out = await resolve_clip_input(
        item, mode="offline",
        proxy_resolver=MagicMock(), archive=MagicMock(),
        cache_only_resolver=cache_only, clip_cache_repo=clip_cache,
        ai_store=MagicMock(), db=MagicMock(), uploads_root=tmp_path,
    )
    assert isinstance(out, ResolvedInput)
    assert out.local_path == p
    assert out.clip_snapshot["name"] == "cached"


@pytest.mark.asyncio
async def test_catdv_offline_no_cache_uses_ai_store(tmp_path):
    item = _Item(kind="catdv_clip", catdv_id="123", display_name="fallback-name")
    cache_only = MagicMock()
    cache_only.path_for_clip_id = AsyncMock(side_effect=FileNotFoundError("nope"))
    ai_store = MagicMock()
    ai_store.status = AsyncMock(return_value=MagicMock())  # any UploadedRef
    ai_store.reference_for_gemini = AsyncMock(return_value={"file_data": {}})
    out = await resolve_clip_input(
        item, mode="offline",
        proxy_resolver=MagicMock(), archive=MagicMock(),
        cache_only_resolver=cache_only, clip_cache_repo=MagicMock(),
        ai_store=ai_store, db=MagicMock(), uploads_root=tmp_path,
    )
    assert isinstance(out, ResolvedInput)
    assert out.local_path is None
    assert out.file_ref is not None
    assert out.clip_snapshot == {"id": "123", "name": "fallback-name"}


@pytest.mark.asyncio
async def test_catdv_fully_unresolvable_returns_unacceptable(tmp_path):
    item = _Item(kind="catdv_clip", catdv_id="123")
    cache_only = MagicMock()
    cache_only.path_for_clip_id = AsyncMock(side_effect=FileNotFoundError())
    ai_store = MagicMock()
    ai_store.status = AsyncMock(return_value=None)
    out = await resolve_clip_input(
        item, mode="offline",
        proxy_resolver=MagicMock(), archive=MagicMock(),
        cache_only_resolver=cache_only, clip_cache_repo=MagicMock(),
        ai_store=ai_store, db=MagicMock(), uploads_root=tmp_path,
    )
    assert isinstance(out, Unacceptable)
    assert "catdv" in out.reason.lower() or "cache" in out.reason.lower()


@pytest.mark.asyncio
async def test_upload_missing_file_returns_unacceptable(tmp_path):
    item = _Item(kind="upload", upload_path="missing.mp4")
    out = await resolve_clip_input(
        item, mode="online",
        proxy_resolver=MagicMock(), archive=MagicMock(),
        cache_only_resolver=MagicMock(), clip_cache_repo=MagicMock(),
        ai_store=MagicMock(), db=MagicMock(),
        uploads_root=tmp_path,
    )
    assert isinstance(out, Unacceptable)
    assert "upload" in out.reason.lower()
