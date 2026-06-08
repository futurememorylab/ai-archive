"""Sets-list render must not be N+1 in the number of sets."""

from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.studio_sets import StudioSetsRepo
from tests._helpers.query_count import assert_query_count


@pytest.fixture
async def db(tmp_path: Path):
    cm = open_db(tmp_path / "test.db")
    conn = await cm.__aenter__()
    await apply_migrations(conn, Path("backend/migrations"))
    yield conn
    await cm.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_sets_list_query_count_flat(db):
    """`list_sets_with_counts` is a single grouped query regardless of N."""
    repo = StudioSetsRepo()
    for i in range(100):
        await repo.create_set(db, name=f"s{i}")
    async with assert_query_count(db, 1):
        await repo.list_sets_with_counts(db, source="archive")
