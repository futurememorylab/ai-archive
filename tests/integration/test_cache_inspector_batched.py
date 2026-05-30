"""Pin the post-T2-1 invariant: CacheInspector.status_for_clips uses a
bounded number of queries regardless of clip count. Without this guard,
a future PR could silently reintroduce per-key loops in the loaders.

The bound: each of the 5 loaders issues ⌈N/400⌉ statements. For N up to
400 clips that's exactly 5; for 1000 clips it's 3 × 5 = 15. We assert
the count is the SAME for 10 vs 100 clips (both under 400) to lock in
the batching."""

from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.services.cache_inspector import CacheInspector
from tests._helpers.query_count import assert_query_count

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


async def _seed_n_clips(conn, n: int, start: int = 0) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    for i in range(start, start + n):
        await conn.execute(
            "INSERT INTO clip_cache(provider_id, provider_clip_id, catalog_id, "
            "name, canonical_json, duration_secs, fps, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            ("catdv", str(i), "1", f"clip {i}", '{"id":' + str(i) + "}", 1.0, 25.0),
        )
        keys.append(("catdv", str(i)))
    await conn.commit()
    return keys


@pytest.mark.asyncio
async def test_status_for_clips_query_count_is_constant_under_400(tmp_path):
    """5 loaders × 1 chunk each = 5 statements for any N ≤ 400."""
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await apply_migrations(conn, MIGRATIONS)

        # 10 clips.
        keys = await _seed_n_clips(conn, 10)
        inspector = CacheInspector(db_provider=lambda: conn)
        async with assert_query_count(conn, max_n=6) as counter:
            await inspector.status_for_clips(keys)
        count_10 = counter.count

        # 100 clips (start from 10 to avoid PK collision with the first 10).
        keys = await _seed_n_clips(conn, 100, start=10)
        async with assert_query_count(conn, max_n=6) as counter:
            await inspector.status_for_clips(keys)
        count_100 = counter.count

        assert count_10 == count_100, (
            f"query count must not scale with N; got {count_10} vs {count_100}"
        )
        # Belt-and-braces: should be exactly 5 (one statement per loader).
        assert count_10 == 5, (
            f"expected 5 statements (one per loader); got {count_10}"
        )


@pytest.mark.asyncio
async def test_status_for_clips_handles_empty_keys(tmp_path):
    """Defensive: empty input must short-circuit without any SQL."""
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await apply_migrations(conn, MIGRATIONS)
        inspector = CacheInspector(db_provider=lambda: conn)
        async with assert_query_count(conn, max_n=0):
            result = await inspector.status_for_clips([])
        assert result == []
