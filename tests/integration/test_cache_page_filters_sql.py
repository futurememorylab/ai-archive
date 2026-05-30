"""cache_page's tab/store/workspace/orphans/evictable filters and its
pagination must happen in SQL, not in Python after hydrating every row.
Today the function loads every cached clip's full status and slices the
result in Python; this regression test asserts the new bounded behavior."""

from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.services.cache_inspector import CacheInspector
from tests._helpers.query_count import assert_query_count

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


async def _seed(conn, n: int):
    for i in range(n):
        await conn.execute(
            "INSERT INTO clip_cache(provider_id, provider_clip_id, catalog_id, "
            "name, canonical_json, duration_secs, fps, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            ("catdv", str(i), "1", f"clip {i}", '{"id":' + str(i) + "}", 1.0, 25.0),
        )
    await conn.commit()


@pytest.mark.asyncio
async def test_list_for_inventory_pagination_uses_sql_limit(tmp_path):
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await apply_migrations(conn, MIGRATIONS)
        await _seed(conn, 1000)

        inspector = CacheInspector(db_provider=lambda: conn)

        async with assert_query_count(conn, max_n=10) as counter:
            rows, total = await inspector.list_for_inventory(
                tab="all", offset=0, limit=50,
            )

        assert total == 1000, "total clip count is over the full set"
        assert len(rows) == 50, "page should be exactly limit"
        # Bounded: ⌈50/400⌉ × 5 loaders + 1 count = 6 statements.
        assert counter.count <= 10, (
            f"got {counter.count} queries for 50-row page over 1000 clips; "
            "must be bounded irrespective of total clip count"
        )


@pytest.mark.asyncio
async def test_list_for_inventory_tab_local_filters_in_sql(tmp_path):
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await apply_migrations(conn, MIGRATIONS)
        await _seed(conn, 100)
        # Add a single proxy_cache row so exactly one clip has media-local.
        await conn.execute(
            "INSERT INTO proxy_cache(provider_id, provider_clip_id, file_path, "
            "size_bytes, downloaded_at, last_used_at) "
            "VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))",
            ("catdv", "5", "/tmp/x.mov", 1000),
        )
        await conn.commit()

        inspector = CacheInspector(db_provider=lambda: conn)
        rows, total = await inspector.list_for_inventory(
            tab="local", offset=0, limit=50,
        )
        assert total == 1, f"tab=local should return only the seeded clip; got total={total}"
        assert len(rows) == 1
        assert rows[0].clip_key == ("catdv", "5")


@pytest.mark.asyncio
async def test_list_for_inventory_orphans_filter(tmp_path):
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await apply_migrations(conn, MIGRATIONS)
        # 5 clip_cache rows, plus a proxy_cache row for a clip with NO
        # clip_cache entry — that's the orphan.
        await _seed(conn, 5)
        await conn.execute(
            "INSERT INTO proxy_cache(provider_id, provider_clip_id, file_path, "
            "size_bytes, downloaded_at, last_used_at) "
            "VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))",
            ("catdv", "999", "/tmp/orphan.mov", 1000),
        )
        await conn.commit()

        inspector = CacheInspector(db_provider=lambda: conn)
        rows, total = await inspector.list_for_inventory(
            tab="all", orphans=True, offset=0, limit=50,
        )
        assert total == 1
        assert rows[0].clip_key == ("catdv", "999")
