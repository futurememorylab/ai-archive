"""assert_query_count counts SQL statements issued against an aiosqlite
connection during an `async with` block. Used as the regression guard
against future N+1 reintroductions."""

from pathlib import Path

import pytest

from backend.app.db import open_db
from tests._helpers.query_count import assert_query_count


@pytest.mark.asyncio
async def test_counts_basic_executes(tmp_path: Path):
    async with open_db(tmp_path / "x.db") as conn:
        await conn.execute("CREATE TABLE t (id INTEGER)")
        async with assert_query_count(conn, max_n=3) as counter:
            await conn.execute("INSERT INTO t VALUES (1)")
            await conn.execute("INSERT INTO t VALUES (2)")
            await conn.execute("INSERT INTO t VALUES (3)")
        assert counter.count == 3


@pytest.mark.asyncio
async def test_exceeding_max_n_raises(tmp_path: Path):
    async with open_db(tmp_path / "x.db") as conn:
        await conn.execute("CREATE TABLE t (id INTEGER)")
        with pytest.raises(AssertionError, match="query count"):
            async with assert_query_count(conn, max_n=2):
                await conn.execute("INSERT INTO t VALUES (1)")
                await conn.execute("INSERT INTO t VALUES (2)")
                await conn.execute("INSERT INTO t VALUES (3)")


@pytest.mark.asyncio
async def test_zero_queries_passes(tmp_path: Path):
    async with open_db(tmp_path / "x.db") as conn:
        async with assert_query_count(conn, max_n=0) as counter:
            pass
        assert counter.count == 0
