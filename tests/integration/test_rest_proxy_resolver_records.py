from pathlib import Path

import pytest

from backend.app.repositories.proxy_cache import ProxyCacheRepo
from backend.app.services.proxy_resolver import RestProxyResolver


class _FakeCatdv:
    """Minimal CatDV client stub: writes a 9-byte file."""

    def __init__(self):
        self.calls = []

    async def download_proxy(self, clip_id: int, dest: Path) -> None:
        self.calls.append((clip_id, dest))
        dest.write_bytes(b"PROXY-OK!")  # noqa: ASYNC240


@pytest.mark.asyncio
async def test_resolver_records_after_download(db, tmp_path):
    repo = ProxyCacheRepo()
    catdv = _FakeCatdv()
    resolver = RestProxyResolver(
        catdv=catdv,
        cache_dir=tmp_path / "cache",
        proxy_cache_repo=repo,
        db_provider=lambda: db,
    )
    path = await resolver.path_for_clip_id(1234)

    assert path.exists() and path.read_bytes() == b"PROXY-OK!"
    row = await repo.get(db, 1234)
    assert row is not None
    assert row["size_bytes"] == 9
    assert row["file_path"] == str(path)


@pytest.mark.asyncio
async def test_resolver_does_not_redownload_or_redouble_record(db, tmp_path):
    repo = ProxyCacheRepo()
    catdv = _FakeCatdv()
    resolver = RestProxyResolver(
        catdv=catdv,
        cache_dir=tmp_path / "cache",
        proxy_cache_repo=repo,
        db_provider=lambda: db,
    )
    await resolver.path_for_clip_id(1234)
    await resolver.path_for_clip_id(1234)
    assert len(catdv.calls) == 1  # cache hit on second call


@pytest.mark.asyncio
async def test_resolver_writes_provider_columns(db, tmp_path):
    """Inspector joins on (provider_id, provider_clip_id); rows without
    those columns are invisible to the badge."""
    repo = ProxyCacheRepo()
    catdv = _FakeCatdv()
    resolver = RestProxyResolver(
        catdv=catdv,
        cache_dir=tmp_path / "cache",
        proxy_cache_repo=repo,
        db_provider=lambda: db,
    )
    await resolver.path_for_clip_id(1234)
    cur = await db.execute(
        "SELECT provider_id, provider_clip_id FROM proxy_cache WHERE catdv_clip_id = ?",
        (1234,),
    )
    assert await cur.fetchone() == ("catdv", "1234")


@pytest.mark.asyncio
async def test_resolver_backfills_legacy_row_with_null_provider(db, tmp_path):
    """A row written by the initial PR 8 record() had empty provider_*
    columns. The resolver must repair such rows on next access instead
    of taking the touch() branch."""
    catdv = _FakeCatdv()
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "7777.mov").write_bytes(b"X" * 42)
    # Simulate the legacy bad row.
    await db.execute(
        "INSERT INTO proxy_cache "
        "(catdv_clip_id, file_path, size_bytes, etag, downloaded_at, last_used_at) "
        "VALUES (?, ?, ?, NULL, ?, ?)",
        (
            7777,
            str(cache_dir / "7777.mov"),
            42,
            "2026-05-20T09:00:00+00:00",
            "2026-05-20T09:00:00+00:00",
        ),
    )
    await db.commit()
    repo = ProxyCacheRepo()
    resolver = RestProxyResolver(
        catdv=catdv,
        cache_dir=cache_dir,
        proxy_cache_repo=repo,
        db_provider=lambda: db,
    )
    await resolver.path_for_clip_id(7777)
    assert catdv.calls == []
    cur = await db.execute(
        "SELECT provider_id, provider_clip_id FROM proxy_cache WHERE catdv_clip_id = ?",
        (7777,),
    )
    assert await cur.fetchone() == ("catdv", "7777")


@pytest.mark.asyncio
async def test_resolver_backfills_preexisting_file(db, tmp_path):
    """A file that landed before this code path runs (or via a previous
    buggy run) must be recorded the first time the resolver sees it."""
    repo = ProxyCacheRepo()
    catdv = _FakeCatdv()
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "5555.mov").write_bytes(b"FAKE-PROXY-FILE")  # 15 bytes
    resolver = RestProxyResolver(
        catdv=catdv,
        cache_dir=cache_dir,
        proxy_cache_repo=repo,
        db_provider=lambda: db,
    )
    await resolver.path_for_clip_id(5555)
    assert catdv.calls == []  # no re-download
    row = await repo.get(db, 5555)
    assert row is not None
    assert row["size_bytes"] == 15
    cur = await db.execute(
        "SELECT provider_id, provider_clip_id FROM proxy_cache WHERE catdv_clip_id = ?",
        (5555,),
    )
    assert await cur.fetchone() == ("catdv", "5555")
