from pathlib import Path

import pytest

from backend.app.repositories.proxy_cache import ProxyCacheRepo
from backend.app.services.proxy_cache_reconciler import ProxyCacheReconciler


def _write_file(path: Path, size: int) -> None:
    path.write_bytes(b"x" * size)


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    d = tmp_path / "proxies"
    d.mkdir()
    return d


def _reconciler(cache_dir: Path, repo: ProxyCacheRepo, db) -> ProxyCacheReconciler:
    return ProxyCacheReconciler(
        cache_dir=cache_dir,
        proxy_cache_repo=repo,
        db_provider=lambda: db,
    )


@pytest.mark.asyncio
async def test_creates_row_for_orphan_file(db, cache_dir):
    repo = ProxyCacheRepo()
    _write_file(cache_dir / "1234.mov", size=4096)

    counters = await _reconciler(cache_dir, repo, db).reconcile()

    assert counters["rows_inserted"] == 1
    row = await repo.get(db, 1234)
    assert row is not None
    assert row["provider_id"] == "catdv"
    assert row["provider_clip_id"] == "1234"
    assert row["size_bytes"] == 4096
    assert row["file_path"].endswith("1234.mov")


@pytest.mark.asyncio
async def test_deletes_phantom_row(db, cache_dir):
    repo = ProxyCacheRepo()
    await repo.record(
        db, clip_id=99, file_path=str(cache_dir / "99.mov"),
        size_bytes=1000, etag=None,
    )

    counters = await _reconciler(cache_dir, repo, db).reconcile()

    assert counters["rows_deleted"] == 1
    assert await repo.get(db, 99) is None


@pytest.mark.asyncio
async def test_updates_drifted_size(db, cache_dir):
    repo = ProxyCacheRepo()
    file_path = cache_dir / "555.mov"
    _write_file(file_path, size=8000)
    await repo.record(
        db, clip_id=555, file_path=str(file_path),
        size_bytes=1000, etag=None,  # stale size
    )

    counters = await _reconciler(cache_dir, repo, db).reconcile()

    assert counters["rows_size_updated"] == 1
    row = await repo.get(db, 555)
    assert row["size_bytes"] == 8000


@pytest.mark.asyncio
async def test_idempotent(db, cache_dir):
    repo = ProxyCacheRepo()
    _write_file(cache_dir / "10.mov", size=100)
    _write_file(cache_dir / "20.mov", size=200)

    first = await _reconciler(cache_dir, repo, db).reconcile()
    second = await _reconciler(cache_dir, repo, db).reconcile()

    assert first["rows_inserted"] == 2
    assert second["rows_inserted"] == 0
    assert second["rows_size_updated"] == 0
    assert second["rows_deleted"] == 0


@pytest.mark.asyncio
async def test_skips_non_integer_filenames(db, cache_dir):
    repo = ProxyCacheRepo()
    _write_file(cache_dir / "weird.mov", size=10)
    (cache_dir / "README.txt").write_text("hi")
    _write_file(cache_dir / "42.mov.part", size=10)

    counters = await _reconciler(cache_dir, repo, db).reconcile()

    assert counters["files_seen"] == 0
    assert counters["rows_inserted"] == 0


@pytest.mark.asyncio
async def test_handles_missing_cache_dir(db, tmp_path):
    repo = ProxyCacheRepo()
    missing = tmp_path / "does-not-exist"

    counters = await _reconciler(missing, repo, db).reconcile()

    # No crash; nothing done.
    assert counters == {
        "files_seen": 0,
        "rows_inserted": 0,
        "rows_size_updated": 0,
        "rows_deleted": 0,
    }


@pytest.mark.asyncio
async def test_skips_zero_byte_file(db, cache_dir):
    repo = ProxyCacheRepo()
    (cache_dir / "7.mov").touch()  # zero bytes

    counters = await _reconciler(cache_dir, repo, db).reconcile()

    assert counters["rows_inserted"] == 0
    assert await repo.get(db, 7) is None
